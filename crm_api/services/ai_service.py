import json
from collections.abc import Callable

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer, Segment
from crm_api.schemas.ai import (
    DraftMessagesRequest,
    DraftMessagesResponse,
    LLMDraftOutput,
    LLMSegmentOutput,
    NLToSegmentResponse,
)
from crm_api.schemas.segments import RuleImpact
from crm_api.services import llm_client, segment_compiler


class SegmentGenerationError(Exception):
    pass


class DraftGenerationError(Exception):
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

    impacts: list[RuleImpact] = []
    warnings: list[str] = []
    for leaf in segment_compiler.collect_leaves(output.definition):
        label = segment_compiler.leaf_label(leaf)
        leaf_count = await session.scalar(
            select(func.count(Customer.id)).where(segment_compiler.compile_leaf(leaf))
        )
        impacts.append(RuleImpact(rule=label, count=leaf_count))
        if leaf_count == 0:
            warnings.append(f"rule '{label}' matches no customers")

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
