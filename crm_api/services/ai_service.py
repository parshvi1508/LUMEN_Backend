import json
import re
import uuid
from collections.abc import Callable

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer, Segment
from crm_api.schemas.ai import (
    DraftMessagesRequest,
    DraftMessagesResponse,
    InsightFact,
    InsightResponse,
    LLMDraftOutput,
    LLMInsightOutput,
    LLMProposalOutput,
    LLMSegmentOutput,
    NLToSegmentResponse,
    ProposeCampaignResponse,
)
from crm_api.schemas.campaigns import CampaignStats
from crm_api.services import (
    llm_client,
    segment_compiler,
    segment_preview,
    stats_service,
)


class SegmentGenerationError(Exception):
    pass


class DraftGenerationError(Exception):
    pass


class InsightGenerationError(Exception):
    pass


class ProposalGenerationError(Exception):
    pass


class SegmentNotFoundError(Exception):
    pass


class _InvalidLLMOutput(Exception):
    pass


async def _generate[T](
    client: httpx.AsyncClient, messages: list[dict[str, str]], parse: Callable[[str], T]
) -> T:
    for attempt in range(2):
        result = await llm_client.complete(client, messages, json_mode=True)
        try:
            return parse(result.text)
        except _InvalidLLMOutput:
            if attempt == 1:
                raise
            messages.append({"role": "assistant", "content": result.text})
            messages.append(
                {
                    "role": "user",
                    "content": "Your previous output was invalid. Return corrected JSON only.",
                }
            )
    raise AssertionError("unreachable")


def _system_prompt() -> str:
    whitelist = segment_compiler.list_whitelist()
    fields = "\n".join(f"  {field}: {', '.join(cmps)}" for field, cmps in whitelist.items())
    return (
        "You translate a marketer request into a customer segment rule tree.\n"
        "Return JSON only, no prose, with exactly two keys: definition and rationale.\n"
        'definition is a rule tree: {"op": "AND" or "OR", "rules": [...]}, where each rule\n'
        'is either another such group or a leaf {"field": ..., "cmp": ..., "value": ...}.\n'
        "Use only these fields and comparators, nothing else:\n"
        f"{fields}\n"
        "Day based comparators take a positive integer of days. is_set, is_not_set, and\n"
        "is_not_set take no meaningful value. rationale is one or two plain sentences\n"
        "explaining the segment. Do not invent fields."
    )


def _parse(text: str) -> LLMSegmentOutput:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _InvalidLLMOutput(f"output was not valid JSON: {exc}") from exc
    try:
        output = LLMSegmentOutput.model_validate(data)
    except ValidationError as exc:
        raise _InvalidLLMOutput(f"output did not match the schema: {exc}") from exc
    try:
        segment_compiler.compile_definition(output.definition)
    except segment_compiler.SegmentCompileError as exc:
        raise _InvalidLLMOutput(f"rule tree rejected by the whitelist: {exc}") from exc
    return output


async def nl_to_segment(
    session: AsyncSession, client: httpx.AsyncClient, prompt: str
) -> NLToSegmentResponse:
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": prompt},
    ]

    try:
        output = await _generate(client, messages, _parse)
    except _InvalidLLMOutput as exc:
        raise SegmentGenerationError(str(exc)) from exc

    where = segment_compiler.compile_definition(output.definition)
    count = await session.scalar(select(func.count(Customer.id)).where(where))

    impacts = await segment_preview.collect_rule_impacts(session, output.definition, count)
    warnings = [
        f"rule '{imp.rule}' matches no customers" for imp in impacts if imp.count == 0
    ]

    return NLToSegmentResponse(
        definition=output.definition.model_dump(),
        rationale=output.rationale,
        count=count,
        per_rule_impact=impacts,
        warnings=warnings,
    )


def _draft_system_prompt(channel: str) -> str:
    norms = {
        "sms": "Keep it under 160 characters, plain text, no links unless essential.",
        "whatsapp": "Conversational and short, one or two sentences, emoji sparingly.",
        "email": "A subject style opener then two or three sentences is fine.",
    }
    return (
        f"You write marketing messages for the {channel} channel. {norms[channel]}\n"
        "Return JSON only, no prose, with one key variants, an array of exactly three items.\n"
        'Each item is {"variant": short label, "message": the draft, "tone": tone label,\n'
        '"reasoning": why this fits the audience}. Messages may use {{first_name}} and\n'
        "{{last_order_amount}} tokens, leave them literal. Do not invent customer data."
    )


def _parse_draft(text: str) -> LLMDraftOutput:
    try:
        return LLMDraftOutput.model_validate_json(text)
    except ValidationError as exc:
        raise _InvalidLLMOutput(f"output did not match the schema: {exc}") from exc


async def draft_messages(
    session: AsyncSession, client: httpx.AsyncClient, payload: DraftMessagesRequest
) -> DraftMessagesResponse:
    segment = await session.get(Segment, payload.segment_id)
    if segment is None:
        raise SegmentNotFoundError(str(payload.segment_id))

    messages = [
        {"role": "system", "content": _draft_system_prompt(payload.channel)},
        {
            "role": "user",
            "content": (
                f"Campaign intent: {payload.campaign_intent}\n"
                f"Target segment: {segment.name}\n"
                f"Segment rules: {json.dumps(segment.definition)}"
            ),
        },
    ]

    try:
        output = await _generate(client, messages, _parse_draft)
    except _InvalidLLMOutput as exc:
        raise DraftGenerationError(str(exc)) from exc

    return DraftMessagesResponse(
        segment_id=payload.segment_id,
        channel=payload.channel,
        variants=output.variants,
    )


_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")


def _insight_facts(stats: CampaignStats) -> list[InsightFact]:
    facts = [InsightFact(label="total", value=stats.total)]
    facts.extend(InsightFact(label=step.status, value=step.count) for step in stats.funnel)
    facts.append(InsightFact(label="failure_rate_pct", value=round(stats.failure_rate * 100)))
    conversion_pct = round(stats.converted / stats.total * 100) if stats.total else 0
    facts.append(InsightFact(label="conversion_rate_pct", value=conversion_pct))
    if stats.audience_size is not None:
        facts.append(InsightFact(label="audience_size", value=stats.audience_size))
    return facts


def _check_grounded(narrative: str, allowed: set[str]) -> None:
    for raw in _NUMBER.findall(narrative.replace(",", "")):
        token = raw.rstrip("%")
        if token.endswith(".0"):
            token = token[:-2]
        if token not in allowed:
            raise _InvalidLLMOutput(f"narrative cited an ungrounded number: {raw}")


async def campaign_insight(
    session: AsyncSession, client: httpx.AsyncClient, campaign_id: uuid.UUID
) -> InsightResponse:
    stats = await stats_service.campaign_stats(session, campaign_id)
    facts = _insight_facts(stats)
    allowed = {str(fact.value) for fact in facts}

    fact_lines = "\n".join(f"{fact.label}: {fact.value}" for fact in facts)
    messages = [
        {
            "role": "system",
            "content": (
                "You summarize one marketing campaign in two or three plain sentences.\n"
                "Cite only the numbers provided, do not invent or compute any other number.\n"
                "Return JSON only, no prose, with one key narrative."
            ),
        },
        {"role": "user", "content": f"Campaign funnel facts:\n{fact_lines}"},
    ]

    def parse(text: str) -> LLMInsightOutput:
        try:
            output = LLMInsightOutput.model_validate_json(text)
        except ValidationError as exc:
            raise _InvalidLLMOutput(f"output did not match the schema: {exc}") from exc
        _check_grounded(output.narrative, allowed)
        return output

    try:
        output = await _generate(client, messages, parse)
    except _InvalidLLMOutput as exc:
        raise InsightGenerationError(str(exc)) from exc

    return InsightResponse(campaign_id=campaign_id, narrative=output.narrative, facts=facts)


def _propose_system_prompt() -> str:
    whitelist = segment_compiler.list_whitelist()
    fields = "\n".join(f"  {field}: {', '.join(cmps)}" for field, cmps in whitelist.items())
    return (
        "You propose a marketing campaign from a stated goal.\n"
        "Return JSON only, no prose, with keys segment, recommended_channel,\n"
        "channel_reasoning, variants. segment is {definition, rationale} where definition\n"
        'is a rule tree {"op": "AND" or "OR", "rules": [...]} of groups and leaves\n'
        '{"field": ..., "cmp": ..., "value": ...}. Use only these fields and comparators:\n'
        f"{fields}\n"
        "recommended_channel is one of whatsapp, sms, email. channel_reasoning explains it.\n"
        "variants is three items, each {variant, message, tone, reasoning}. Messages may use\n"
        "{{first_name}} and {{last_order_amount}} tokens, leave them literal. Do not invent fields."
    )


def _parse_proposal(text: str) -> LLMProposalOutput:
    try:
        output = LLMProposalOutput.model_validate_json(text)
    except ValidationError as exc:
        raise _InvalidLLMOutput(f"output did not match the schema: {exc}") from exc
    try:
        segment_compiler.compile_definition(output.segment.definition)
    except segment_compiler.SegmentCompileError as exc:
        raise _InvalidLLMOutput(f"rule tree rejected by the whitelist: {exc}") from exc
    return output


async def propose_campaign(
    session: AsyncSession, client: httpx.AsyncClient, goal: str
) -> ProposeCampaignResponse:
    messages = [
        {"role": "system", "content": _propose_system_prompt()},
        {"role": "user", "content": f"Goal: {goal}"},
    ]

    try:
        output = await _generate(client, messages, _parse_proposal)
    except _InvalidLLMOutput as exc:
        raise ProposalGenerationError(str(exc)) from exc

    # Proposal only - persist NOTHING until the marketer approves. Persisting on
    # every propose flooded the segments/campaigns lists with duplicates.
    where = segment_compiler.compile_definition(output.segment.definition)
    count = await session.scalar(select(func.count(Customer.id)).where(where))

    return ProposeCampaignResponse(
        campaign_id=None,
        proposal_state="draft",
        goal=goal,
        segment_definition=output.segment.definition.model_dump(),
        segment_rationale=output.segment.rationale,
        audience_size=count,
        recommended_channel=output.recommended_channel,
        channel_reasoning=output.channel_reasoning,
        variants=output.variants,
    )
