from sqlalchemy import func, select

from crm_api.models import Communication
from crm_api.services.campaign_service import render_message

SEGMENT_DEF = {
    "op": "AND",
    "rules": [
        {"field": "total_spend", "cmp": "gte", "value": 5000},
        {"field": "city", "cmp": "eq", "value": "Mumbai"},
    ],
}


async def create_segment(client, definition=None, name="big mumbai spenders"):
    resp = await client.post(
        "/api/v1/segments", json={"name": name, "definition": definition or SEGMENT_DEF}
    )
    assert resp.status_code == 201
    return resp.json()


async def test_campaign_materializes_audience(client, db_session) -> None:
    segment = await create_segment(client)
    preview = await client.post("/api/v1/segments/preview", json={"definition": SEGMENT_DEF})
    expected_count = preview.json()["count"]

    resp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "winback",
            "segment_id": segment["id"],
            "channel": "email",
            "message_template": "Hi {{first_name}}, we miss you.",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["audience_size"] == expected_count

    rows = (
        await db_session.execute(
            select(Communication.status, Communication.status_rank, func.count())
            .where(Communication.campaign_id == body["id"])
            .group_by(Communication.status, Communication.status_rank)
        )
    ).all()
    assert rows == [("queued", 0, expected_count)]


async def test_rendered_message_exact(client, db_session) -> None:
    await client.post(
        "/api/v1/customers/bulk",
        json={"customers": [{"external_id": "rc1", "name": "Meera Iyer", "city": "Goa City"}]},
    )
    await client.post(
        "/api/v1/orders/bulk",
        json={
            "orders": [
                {
                    "external_id": "ro1",
                    "customer_external_id": "rc1",
                    "amount": "450.00",
                    "ordered_at": "2026-05-01T10:00:00Z",
                },
                {
                    "external_id": "ro2",
                    "customer_external_id": "rc1",
                    "amount": "725.50",
                    "ordered_at": "2026-06-01T10:00:00Z",
                },
            ]
        },
    )
    segment = await create_segment(
        client,
        definition={"op": "AND", "rules": [{"field": "city", "cmp": "eq", "value": "Goa City"}]},
        name="goa",
    )
    resp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "goa promo",
            "segment_id": segment["id"],
            "channel": "sms",
            "message_template": "Hi {{first_name}}, your last order was {{last_order_amount}}.",
        },
    )
    assert resp.status_code == 201
    message = await db_session.scalar(
        select(Communication.rendered_message).where(Communication.campaign_id == resp.json()["id"])
    )
    assert message == "Hi Meera, your last order was 725.50."


def test_unknown_token_stays_literal() -> None:
    rendered = render_message("Hi {{first_name}}, code {{promo_code}}", {"first_name": "Asha"})
    assert rendered == "Hi Asha, code {{promo_code}}"


def test_none_field_renders_empty() -> None:
    assert render_message("City: {{city}}.", {"city": None}) == "City: ."


async def test_list_campaigns_newest_first(client) -> None:
    segment = await create_segment(client, name="list seg")
    for nm in ("c_old", "c_new"):
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": nm,
                "segment_id": segment["id"],
                "channel": "email",
                "message_template": "hi",
            },
        )
        assert resp.status_code == 201

    resp = await client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    names = [c["name"] for c in body]
    assert "c_old" in names and "c_new" in names
    # newest first: c_new appears before c_old
    assert names.index("c_new") < names.index("c_old")


async def test_campaign_unknown_segment_404(client) -> None:
    resp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "x",
            "segment_id": "00000000-0000-0000-0000-000000000000",
            "channel": "email",
            "message_template": "hi",
        },
    )
    assert resp.status_code == 404


async def test_campaign_invalid_channel_422(client) -> None:
    segment = await create_segment(client)
    resp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "x",
            "segment_id": segment["id"],
            "channel": "pigeon",
            "message_template": "hi",
        },
    )
    assert resp.status_code == 422


async def test_segment_create_rejects_off_whitelist(client, db_session) -> None:
    resp = await client.post(
        "/api/v1/segments",
        json={
            "name": "evil",
            "definition": {
                "op": "AND",
                "rules": [{"field": "password", "cmp": "eq", "value": "x"}],
            },
        },
    )
    assert resp.status_code == 422
    from crm_api.models import Segment

    count = await db_session.scalar(select(func.count(Segment.id)).where(Segment.name == "evil"))
    assert count == 0


async def test_empty_audience_allowed(client, db_session) -> None:
    segment = await create_segment(
        client,
        definition={
            "op": "AND",
            "rules": [{"field": "total_spend", "cmp": "gt", "value": 1000000000}],
        },
        name="nobody",
    )
    resp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "ghost town",
            "segment_id": segment["id"],
            "channel": "email",
            "message_template": "hi",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["audience_size"] == 0
    count = await db_session.scalar(
        select(func.count(Communication.id)).where(Communication.campaign_id == resp.json()["id"])
    )
    assert count == 0
