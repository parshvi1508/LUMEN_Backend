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
