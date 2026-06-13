from decimal import Decimal

from sqlalchemy import and_, func, or_, select

from crm_api.models import Customer
from crm_api.schemas.segments import RuleGroup
from crm_api.services import segment_compiler

INJECTION = "x'; DROP TABLE customers; --"


def ast(*rules, op="AND"):
    return {"op": op, "rules": list(rules)}


def leaf(field, cmp, value=None):
    return {"field": field, "cmp": cmp, "value": value}


async def test_and_tree_count_matches_orm(client, db_session) -> None:
    expected = await db_session.scalar(
        select(func.count(Customer.id)).where(
            and_(Customer.total_spend >= Decimal(5000), Customer.city == "Mumbai")
        )
    )
    resp = await client.post(
        "/api/v1/segments/preview",
        json={"definition": ast(leaf("total_spend", "gte", 5000), leaf("city", "eq", "Mumbai"))},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == expected
    assert len(body["sample"]) <= 10


async def test_or_tree_count_matches_orm(client, db_session) -> None:
    expected = await db_session.scalar(
        select(func.count(Customer.id)).where(
            or_(Customer.city == "Delhi", Customer.city == "Pune")
        )
    )
    resp = await client.post(
        "/api/v1/segments/preview",
        json={"definition": ast(leaf("city", "eq", "Delhi"), leaf("city", "eq", "Pune"), op="OR")},
    )
    assert resp.json()["count"] == expected


async def test_nested_group_count_matches_orm(client, db_session) -> None:
    expected = await db_session.scalar(
        select(func.count(Customer.id)).where(
            and_(
                Customer.order_count >= 2,
                or_(Customer.city == "Delhi", Customer.city == "Mumbai"),
            )
        )
    )
    resp = await client.post(
        "/api/v1/segments/preview",
        json={
            "definition": ast(
                leaf("order_count", "gte", 2),
                ast(leaf("city", "eq", "Delhi"), leaf("city", "eq", "Mumbai"), op="OR"),
            )
        },
    )
    assert resp.json()["count"] == expected


async def test_whitelist_rejections(client) -> None:
    cases = [
        leaf("password", "eq", "x"),
        leaf("total_spend", "regex", ".*"),
        leaf("city", "gt", "Mumbai"),
        leaf("last_order_at", "older_than_days", -5),
        leaf("city", "in_list", ["a"] * 51),
    ]
    for bad in cases:
        resp = await client.post("/api/v1/segments/preview", json={"definition": ast(bad)})
        assert resp.status_code == 422, bad


async def test_injection_lands_as_bound_parameter(client, db_session) -> None:
    group = RuleGroup.model_validate(ast(leaf("city", "eq", INJECTION)))
    where = segment_compiler.compile_definition(group)
    compiled = select(Customer).where(where).compile()
    assert INJECTION in compiled.params.values()
    assert INJECTION not in str(compiled)

    resp = await client.post(
        "/api/v1/segments/preview", json={"definition": ast(leaf("city", "eq", INJECTION))}
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    still_there = await db_session.scalar(select(func.count(Customer.id)))
    assert still_there > 0


async def test_structural_caps(client) -> None:
    deep = leaf("city", "eq", "Pune")
    for _ in range(6):
        deep = ast(deep)
    resp = await client.post("/api/v1/segments/preview", json={"definition": deep})
    assert resp.status_code == 422

    wide = ast(*[leaf("city", "eq", "Pune") for _ in range(51)])
    resp = await client.post("/api/v1/segments/preview", json={"definition": wide})
    assert resp.status_code == 422


async def test_per_rule_impact(client) -> None:
    resp = await client.post(
        "/api/v1/segments/preview",
        json={"definition": ast(leaf("total_spend", "gte", 5000), leaf("city", "eq", "Mumbai"))},
    )
    body = resp.json()
    assert len(body["per_rule_impact"]) == 2
    for impact in body["per_rule_impact"]:
        assert impact["count"] >= body["count"]


async def test_lapsed_cohort_on_seed_data(client) -> None:
    resp = await client.post(
        "/api/v1/segments/preview",
        json={"definition": ast(leaf("last_order_at", "older_than_days", 90))},
    )
    assert resp.json()["count"] >= 150


# ── marginal per_rule_impact: pure helpers (no DB) ──
def test_walk_leaves_with_path_pure() -> None:
    group = RuleGroup.model_validate(
        ast(
            leaf("total_spend", "gte", 5000),
            ast(leaf("city", "eq", "Delhi"), leaf("city", "eq", "Mumbai"), op="OR"),
        )
    )
    paths = [p for _, p in segment_compiler.walk_leaves_with_path(group)]
    assert paths == [[0], [1, 0], [1, 1]]


def test_remove_at_path_pure() -> None:
    group = RuleGroup.model_validate(
        ast(
            leaf("total_spend", "gte", 5000),
            ast(leaf("city", "eq", "Delhi"), leaf("city", "eq", "Mumbai"), op="OR"),
        )
    )
    # drop the top-level spend leaf -> only the OR subtree remains
    pruned = segment_compiler.remove_at_path(group, [0])
    assert pruned is not None and len(pruned.rules) == 1

    # drop one OR alternative -> OR shrinks to a single leaf
    once = segment_compiler.remove_at_path(group, [1, 0])
    assert once is not None
    inner = once.rules[1]
    assert isinstance(inner, RuleGroup) and len(inner.rules) == 1

    # drop the remaining OR alternative -> empty OR collapses upward
    twice = segment_compiler.remove_at_path(once, [1, 0])
    assert twice is not None and len(twice.rules) == 1

    # single-leaf root collapses to None (no constraints left)
    single = RuleGroup.model_validate(ast(leaf("city", "eq", "Delhi")))
    assert segment_compiler.remove_at_path(single, [0]) is None


# ── marginal per_rule_impact: API (DB-backed) ──
async def test_marginal_impact_under_and(client, db_session) -> None:
    defn = ast(leaf("total_spend", "gte", 5000), leaf("city", "eq", "Mumbai"))
    body = (
        await client.post("/api/v1/segments/preview", json={"definition": defn})
    ).json()
    full = body["count"]

    only_spend = await db_session.scalar(
        select(func.count(Customer.id)).where(Customer.total_spend >= Decimal(5000))
    )
    only_city = await db_session.scalar(
        select(func.count(Customer.id)).where(Customer.city == "Mumbai")
    )
    by_path = {tuple(i["path"]): i for i in body["per_rule_impact"]}

    # removing the city leaf [1] leaves AND(total_spend) == only_spend
    assert by_path[(1,)]["audience_without"] == only_spend
    # removing the spend leaf [0] leaves AND(city) == only_city
    assert by_path[(0,)]["audience_without"] == only_city
    for imp in body["per_rule_impact"]:
        assert imp["marginal"] >= 0
        assert imp["audience_without"] - full == imp["marginal"]


async def test_marginal_single_leaf_is_total_minus_full(client, db_session) -> None:
    defn = ast(leaf("city", "eq", "Mumbai"))
    body = (
        await client.post("/api/v1/segments/preview", json={"definition": defn})
    ).json()
    total = await db_session.scalar(select(func.count(Customer.id)))
    imp = body["per_rule_impact"][0]
    assert imp["audience_without"] == total
    assert imp["marginal"] == total - body["count"]


async def test_duplicate_leaves_get_distinct_paths(client) -> None:
    defn = ast(
        leaf("city", "eq", "Delhi"), leaf("city", "eq", "Delhi"), op="OR"
    )
    body = (
        await client.post("/api/v1/segments/preview", json={"definition": defn})
    ).json()
    impacts = body["per_rule_impact"]
    assert len(impacts) == 2
    assert sorted(tuple(i["path"]) for i in impacts) == [(0,), (1,)]
