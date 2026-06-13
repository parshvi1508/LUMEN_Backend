import json

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer
from crm_api.schemas.ai import LLMSegmentOutput, NLToSegmentResponse
from crm_api.schemas.segments import RuleImpact
from crm_api.services import llm_client, segment_compiler


class SegmentGenerationError(Exception):
    pass


class _InvalidLLMOutput(Exception):
    pass


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

    output: LLMSegmentOutput | None = None
    for attempt in range(2):
        result = await llm_client.complete(client, messages, json_mode=True)
        try:
            output = _parse(result.text)
            break
        except _InvalidLLMOutput as exc:
            if attempt == 1:
                raise SegmentGenerationError(str(exc)) from exc
            messages.append({"role": "assistant", "content": result.text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous output was invalid: {exc}. Return corrected JSON only."
                    ),
                }
            )

    assert output is not None
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
