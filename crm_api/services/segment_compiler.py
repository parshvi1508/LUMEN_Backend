"""The single whitelist for segment rule fields and comparators.

Adding a field means adding it to WHITELIST here plus a test. No other module
may map rule input to SQL. Values only ever become bound parameters.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from crm_api.models import Customer
from crm_api.schemas.segments import RuleGroup, RuleLeaf

MAX_DEPTH = 5
MAX_LEAVES = 50


class SegmentCompileError(ValueError):
    pass


def _as_number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise SegmentCompileError(f"expected a number, got {type(value).__name__}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise SegmentCompileError(f"not a valid number: {value!r}") from exc


def _as_days(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SegmentCompileError(f"expected a positive integer of days, got {value!r}")
    return value


def _as_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SegmentCompileError(f"expected a non-empty string, got {value!r}")
    return value


def _as_str_list(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 50
        or not all(isinstance(v, str) and v for v in value)
    ):
        raise SegmentCompileError("expected a non-empty list of strings, max 50")
    return value


def _cutoff(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _numeric_cmps(col: InstrumentedAttribute) -> dict[str, Callable[[object], ColumnElement]]:
    return {
        "eq": lambda v: col == _as_number(v),
        "gt": lambda v: col > _as_number(v),
        "gte": lambda v: col >= _as_number(v),
        "lt": lambda v: col < _as_number(v),
        "lte": lambda v: col <= _as_number(v),
    }


WHITELIST: dict[str, dict[str, Callable[[object], ColumnElement]]] = {
    "total_spend": _numeric_cmps(Customer.total_spend),
    "order_count": _numeric_cmps(Customer.order_count),
    "city": {
        "eq": lambda v: Customer.city == _as_str(v),
        "neq": lambda v: Customer.city != _as_str(v),
        "in_list": lambda v: Customer.city.in_(_as_str_list(v)),
    },
    "email": {
        "is_set": lambda v: Customer.email.is_not(None),
        "is_not_set": lambda v: Customer.email.is_(None),
    },
    "last_order_at": {
        "older_than_days": lambda v: Customer.last_order_at < _cutoff(_as_days(v)),
        "within_days": lambda v: Customer.last_order_at >= _cutoff(_as_days(v)),
        "is_not_set": lambda v: Customer.last_order_at.is_(None),
    },
    "created_at": {
        "older_than_days": lambda v: Customer.created_at < _cutoff(_as_days(v)),
        "within_days": lambda v: Customer.created_at >= _cutoff(_as_days(v)),
    },
}


def list_whitelist() -> dict[str, list[str]]:
    return {field: sorted(cmps) for field, cmps in WHITELIST.items()}


def compile_leaf(leaf: RuleLeaf) -> ColumnElement:
    cmps = WHITELIST.get(leaf.field)
    if cmps is None:
        raise SegmentCompileError(f"field not allowed: {leaf.field!r}")
    builder = cmps.get(leaf.cmp)
    if builder is None:
        raise SegmentCompileError(f"comparator not allowed for {leaf.field!r}: {leaf.cmp!r}")
    return builder(leaf.value)


def collect_leaves(group: RuleGroup) -> list[RuleLeaf]:
    leaves: list[RuleLeaf] = []
    for rule in group.rules:
        if isinstance(rule, RuleGroup):
            leaves.extend(collect_leaves(rule))
        else:
            leaves.append(rule)
    return leaves


def leaf_label(leaf: RuleLeaf) -> str:
    return f"{leaf.field} {leaf.cmp} {leaf.value!r}"


def walk_leaves_with_path(
    group: RuleGroup, _prefix: tuple[int, ...] = ()
) -> list[tuple[RuleLeaf, list[int]]]:
    """Every leaf paired with its index path from the root. Pure, no SQL."""
    out: list[tuple[RuleLeaf, list[int]]] = []
    for i, rule in enumerate(group.rules):
        if isinstance(rule, RuleGroup):
            out.extend(walk_leaves_with_path(rule, (*_prefix, i)))
        else:
            out.append((rule, [*_prefix, i]))
    return out


def remove_at_path(group: RuleGroup, path: list[int]) -> RuleGroup | None:
    """Return ``group`` with the node at ``path`` removed.

    Empty groups collapse upward; if the root would empty, returns None
    (meaning: no constraints left -> the whole customer base). Pure, no SQL.
    """
    head, *rest = path
    new_rules: list[RuleGroup | RuleLeaf] = []
    for idx, rule in enumerate(group.rules):
        if idx != head:
            new_rules.append(rule)
            continue
        if rest:
            if not isinstance(rule, RuleGroup):
                raise SegmentCompileError("path does not point at a group")
            pruned = remove_at_path(rule, rest)
            if pruned is not None:
                new_rules.append(pruned)
        # rest empty -> this index is the target node; drop it
    if not new_rules:
        return None
    return RuleGroup(op=group.op, rules=new_rules)


def compile_definition(group: RuleGroup, _depth: int = 1) -> ColumnElement:
    if _depth > MAX_DEPTH:
        raise SegmentCompileError(f"rule tree deeper than {MAX_DEPTH} levels")
    if _depth == 1 and len(collect_leaves(group)) > MAX_LEAVES:
        raise SegmentCompileError(f"more than {MAX_LEAVES} rules")
    clauses = [
        compile_definition(rule, _depth + 1) if isinstance(rule, RuleGroup) else compile_leaf(rule)
        for rule in group.rules
    ]
    return and_(*clauses) if group.op == "AND" else or_(*clauses)
