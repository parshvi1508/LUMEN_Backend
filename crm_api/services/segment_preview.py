"""Per-rule impact computation for segment previews.

Counting lives here (one place that runs the queries); the AST walking/pruning
is pure and lives in segment_compiler. All predicates still go through the
compile_* whitelist path — no raw input reaches SQL.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer
from crm_api.schemas.segments import RuleGroup, RuleImpact
from crm_api.services import segment_compiler


async def _count(session: AsyncSession, where=None) -> int:
    stmt = select(func.count(Customer.id))
    if where is not None:
        stmt = stmt.where(where)
    return await session.scalar(stmt)


async def collect_rule_impacts(
    session: AsyncSession, definition: RuleGroup, full: int
) -> list[RuleImpact]:
    """Standalone + marginal impact for every leaf in ``definition``.

    ``full`` is the audience of the complete definition (already computed by the
    caller). ``marginal = audience_without - full``: positive under AND
    (the rule excludes customers), negative under OR (the rule is their only path in).
    """
    total = await _count(session)
    impacts: list[RuleImpact] = []
    for leaf, path in segment_compiler.walk_leaves_with_path(definition):
        standalone = await _count(session, segment_compiler.compile_leaf(leaf))
        pruned = segment_compiler.remove_at_path(definition, path)
        without = (
            total
            if pruned is None
            else await _count(session, segment_compiler.compile_definition(pruned))
        )
        impacts.append(
            RuleImpact(
                rule=segment_compiler.leaf_label(leaf),
                path=path,
                count=standalone,
                audience_without=without,
                marginal=without - full,
            )
        )
    return impacts
