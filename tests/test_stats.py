import uuid

from sqlalchemy import func, select

from crm_api.models import Communication
from crm_api.services.receipt_service import STATUS_RANKS
from tests.test_dispatch import seed_campaign
from tests.test_receipts import event, post_receipts


async def get_stats(client, campaign_id: str):
    return await client.get(f"/api/v1/campaigns/{campaign_id}/stats")


async def comm_ids(db_session, campaign_id: str) -> list[str]:
    rows = await db_session.scalars(
        select(Communication.id).where(Communication.campaign_id == uuid.UUID(campaign_id))
    )
    return [str(comm_id) for comm_id in rows]


async def test_stats_unknown_campaign_404(client) -> None:
    resp = await get_stats(client, "00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_stats_fresh_campaign_all_queued(client) -> None:
    campaign = await seed_campaign(client, 3)

    resp = await get_stats(client, campaign["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == campaign["id"]
    assert body["audience_size"] == 3
    assert body["total"] == 3
    assert body["failed"] == 0
    assert body["failure_rate"] == 0.0
    assert body["converted"] == 0

    by_status = {step["status"]: step for step in body["funnel"]}
    assert set(by_status) == set(STATUS_RANKS)
    assert [step["rank"] for step in body["funnel"]] == sorted(STATUS_RANKS.values())
    assert by_status["queued"]["count"] == 3
    assert all(step["count"] == 0 for s, step in by_status.items() if s != "queued")


async def test_stats_reconcile_with_communications(client, db_session) -> None:
    campaign = await seed_campaign(client, 4)
    ids = await comm_ids(db_session, campaign["id"])

    resp = await post_receipts(
        client,
        [
            event(ids[0], "sent"),
            event(ids[0], "delivered"),
            event(ids[1], "failed"),
        ],
    )
    assert resp.status_code == 200

    stats = (await get_stats(client, campaign["id"])).json()
    by_status = {step["status"]: step["count"] for step in stats["funnel"]}
    assert by_status["queued"] == 2
    assert by_status["delivered"] == 1
    assert by_status["failed"] == 1
    assert stats["total"] == 4
    assert stats["failed"] == 1
    assert stats["failure_rate"] == 0.25

    db_counts = dict(
        (
            await db_session.execute(
                select(Communication.status, func.count())
                .where(Communication.campaign_id == uuid.UUID(campaign["id"]))
                .group_by(Communication.status)
            )
        ).all()
    )
    for step in stats["funnel"]:
        assert step["count"] == db_counts.get(step["status"], 0)
    assert sum(by_status.values()) == stats["total"]


async def test_stats_converted_count(client, db_session) -> None:
    campaign = await seed_campaign(client, 2)
    ids = await comm_ids(db_session, campaign["id"])

    resp = await post_receipts(client, [event(ids[0], "converted")])
    assert resp.status_code == 200

    stats = (await get_stats(client, campaign["id"])).json()
    assert stats["converted"] == 1
    by_status = {step["status"]: step["count"] for step in stats["funnel"]}
    assert by_status["converted"] == 1
    assert by_status["queued"] == 1


async def test_stats_empty_audience(client) -> None:
    campaign = await seed_campaign(client, 0)

    stats = (await get_stats(client, campaign["id"])).json()
    assert stats["total"] == 0
    assert stats["failure_rate"] == 0.0
    assert all(step["count"] == 0 for step in stats["funnel"])
