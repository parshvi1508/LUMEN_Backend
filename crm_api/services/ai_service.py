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
    warnings = [f"rule '{imp.rule}' matches no customers" for imp in impacts if imp.count == 0]

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


MAX_AGENT_ITERATIONS = 6

AGENT_TOOLS = [
    {
        "name": "query_segment",
        "description": "Count how many customers match a segment definition and return a sample.",
        "parameters": {"definition": "A rule tree with op and rules."},
        "error_hint": "If count is 0, broaden your rules. If very large, narrow them.",
    },
    {
        "name": "score_risk",
        "description": (
            "Get risk scores and SHAP reasons for customers in a segment. "
            "Returns avg reactivation probability, tier breakdown, and top reasons."
        ),
        "parameters": {"definition": "A rule tree with op and rules."},
        "error_hint": "Scores may not exist for all customers. Check customers_found.",
    },
    {
        "name": "estimate_campaign",
        "description": (
            "Estimate cost and potential revenue for a campaign targeting a segment via a channel."
        ),
        "parameters": {
            "definition": "A rule tree with op and rules.",
            "channel": "whatsapp, sms, or email",
        },
        "error_hint": "Revenue estimate uses expected values from ML scores.",
    },
]


def _agent_system_prompt() -> str:
    whitelist = segment_compiler.list_whitelist()
    fields = "\n".join(f"  {field}: {', '.join(cmps)}" for field, cmps in whitelist.items())
    tool_desc = "\n".join(
        f"  - {t['name']}: {t['description']} Parameters: {json.dumps(t['parameters'])}"
        for t in AGENT_TOOLS
    )
    return (
        "You are a campaign planning agent. Given a marketing goal, you explore "
        "the customer base using tools before proposing a campaign.\n\n"
        "Available tools:\n" + tool_desc + "\n\n"
        'To call a tool, return JSON: {"action": "tool", "tool": "<name>", '
        '"args": {<params>}}\n'
        'When ready to propose, return JSON: {"action": "propose", "proposal": {<proposal>}}\n\n'
        "The proposal object must have keys: segment (with definition and rationale), "
        "recommended_channel, channel_reasoning, variants (three items, each with "
        "variant, message, tone, reasoning).\n"
        'Segment definitions are rule trees: {"op": "AND" or "OR", "rules": [...]}, '
        'leaves are {"field": ..., "cmp": ..., "value": ...}.\n'
        "Use only these fields and comparators:\n" + fields + "\n\n"
        "You MUST call at least 2 tools before proposing. Messages may use "
        "{{first_name}} and {{last_order_amount}} tokens. Do not invent fields.\n"
        "Return JSON only, no prose."
    )


async def _execute_tool(session: AsyncSession, tool_name: str, args: dict) -> dict:
    """Execute an agent tool and return the result as a dict."""
    if tool_name == "query_segment":
        definition = segment_compiler.RuleGroup.model_validate(args["definition"])
        segment_compiler.compile_definition(definition)
        where = segment_compiler.compile_definition(definition)
        count = await session.scalar(select(func.count(Customer.id)).where(where))
        sample_rows = await session.scalars(
            select(Customer).where(where).order_by(Customer.total_spend.desc()).limit(5)
        )
        sample = [
            {"name": c.name, "total_spend": float(c.total_spend), "city": c.city}
            for c in sample_rows
        ]
        return {"count": count, "sample": sample}

    if tool_name == "score_risk":
        from crm_api.models import CustomerScore

        definition = segment_compiler.RuleGroup.model_validate(args["definition"])
        where = segment_compiler.compile_definition(definition)
        customer_ids = list(await session.scalars(select(Customer.id).where(where).limit(500)))
        if not customer_ids:
            return {"customers_found": 0, "message": "No customers match this segment."}
        scores = (
            (
                await session.execute(
                    select(CustomerScore).where(CustomerScore.customer_id.in_(customer_ids))
                )
            )
            .scalars()
            .all()
        )
        if not scores:
            return {
                "customers_found": len(customer_ids),
                "scored": 0,
                "message": "No ML scores available for these customers.",
            }
        avg_prob = sum(float(s.reactivation_probability) for s in scores) / len(scores)
        tiers = {}
        for s in scores:
            tiers[s.value_tier] = tiers.get(s.value_tier, 0) + 1
        all_reasons = []
        for s in scores:
            if s.reasons:
                all_reasons.extend(s.reasons[:2])
        top_reasons = {}
        for r in all_reasons:
            feature = r.get("feature", r) if isinstance(r, dict) else str(r)
            top_reasons[feature] = top_reasons.get(feature, 0) + 1
        sorted_reasons = sorted(top_reasons.items(), key=lambda kv: -kv[1])[:3]
        return {
            "customers_found": len(customer_ids),
            "scored": len(scores),
            "avg_reactivation_probability": round(avg_prob, 4),
            "tier_breakdown": tiers,
            "top_risk_drivers": [{"feature": f, "frequency": c} for f, c in sorted_reasons],
        }

    if tool_name == "estimate_campaign":
        from crm_api.models import CustomerScore
        from crm_api.services.economics import DEFAULT_CHANNEL_COST

        definition = segment_compiler.RuleGroup.model_validate(args["definition"])
        channel = args.get("channel", "email")
        where = segment_compiler.compile_definition(definition)
        count = await session.scalar(select(func.count(Customer.id)).where(where))
        unit_cost = float(DEFAULT_CHANNEL_COST.get(channel, DEFAULT_CHANNEL_COST["email"]))
        total_cost = round(unit_cost * (count or 0), 2)
        customer_ids = list(await session.scalars(select(Customer.id).where(where).limit(500)))
        expected_revenue = 0.0
        if customer_ids:
            scores = (
                (
                    await session.execute(
                        select(CustomerScore).where(CustomerScore.customer_id.in_(customer_ids))
                    )
                )
                .scalars()
                .all()
            )
            expected_revenue = round(sum(float(s.expected_value) for s in scores), 2)
        return {
            "audience_size": count,
            "channel": channel,
            "estimated_cost": total_cost,
            "estimated_revenue": expected_revenue,
            "estimated_roi": round((expected_revenue - total_cost) / max(total_cost, 0.01), 2),
        }

    return {"error": f"Unknown tool: {tool_name}"}


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
        {"role": "system", "content": _agent_system_prompt()},
        {"role": "user", "content": f"Goal: {goal}"},
    ]

    tool_trace: list[dict] = []
    seen_calls: list[str] = []
    tools_called = 0
    expected_impact = None

    for iteration in range(MAX_AGENT_ITERATIONS):
        result = await llm_client.complete(client, messages, json_mode=True)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ProposalGenerationError(f"Agent returned invalid JSON: {exc}") from exc

        action = data.get("action", "")

        if action == "propose":
            if tools_called < 2:
                messages.append({"role": "assistant", "content": result.text})
                messages.append(
                    {
                        "role": "user",
                        "content": "You must call at least 2 tools before proposing. "
                        "Use query_segment and score_risk first.",
                    }
                )
                continue
            proposal_data = data.get("proposal", data)
            try:
                output = LLMProposalOutput.model_validate(proposal_data)
            except ValidationError as exc:
                raise ProposalGenerationError(f"Proposal did not match schema: {exc}") from exc
            try:
                segment_compiler.compile_definition(output.segment.definition)
            except segment_compiler.SegmentCompileError as exc:
                raise ProposalGenerationError(f"Rule tree rejected by whitelist: {exc}") from exc

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
                tool_trace=tool_trace,
                expected_impact=expected_impact,
            )

        if action == "tool":
            tool_name = data.get("tool", "")
            tool_args = data.get("args", {})

            # Stuck detection: same tool + same args seen before
            call_key = json.dumps({"tool": tool_name, "args": tool_args}, sort_keys=True)
            if call_key in seen_calls:
                messages.append({"role": "assistant", "content": result.text})
                messages.append(
                    {
                        "role": "user",
                        "content": "You already called this tool with these exact arguments. "
                        "Try different parameters or propose your campaign.",
                    }
                )
                continue
            seen_calls.append(call_key)

            try:
                tool_result = await _execute_tool(session, tool_name, tool_args)
            except Exception as exc:
                tool_hint = next(
                    (t["error_hint"] for t in AGENT_TOOLS if t["name"] == tool_name),
                    "Check your parameters.",
                )
                tool_result = {"error": str(exc), "hint": tool_hint}

            trace_entry = {
                "iteration": iteration,
                "tool": tool_name,
                "args": tool_args,
                "result": tool_result,
            }
            tool_trace.append(trace_entry)
            tools_called += 1

            if tool_name == "estimate_campaign" and "error" not in tool_result:
                expected_impact = tool_result

            messages.append({"role": "assistant", "content": result.text})
            messages.append(
                {
                    "role": "user",
                    "content": f"Tool result: {json.dumps(tool_result)}\n"
                    "Call another tool or propose your campaign.",
                }
            )
            continue

        # Unknown action - ask agent to correct
        messages.append({"role": "assistant", "content": result.text})
        messages.append(
            {
                "role": "user",
                "content": (
                    'Invalid action. Use {"action": "tool", ...} or {"action": "propose", ...}.'
                ),
            }
        )

    raise ProposalGenerationError(
        f"Agent did not produce a proposal within {MAX_AGENT_ITERATIONS} iterations"
    )
