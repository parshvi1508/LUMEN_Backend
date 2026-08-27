import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crm_api.models import Customer, Tenant
from crm_api.schemas.campaigns import WinBackRequest
from crm_api.services.campaign_service import create_winback_campaign


async def _setup(session):
    tid = uuid.uuid4()
    session.add(Tenant(id=tid, name="wb-test"))
    await session.flush()

    now = datetime.now(UTC)
    lapsed = now - timedelta(days=200)
    recent = now - timedelta(days=30)

    customers = [
        Customer(
            id=uuid.uuid4(),
            tenant_id=tid,
            name="Lapsed High",
            external_id=f"lh-{tid}",
            total_spend=Decimal("5000"),
            last_order_at=lapsed,
        ),
        Customer(
            id=uuid.uuid4(),
            tenant_id=tid,
            name="Lapsed Low",
            external_id=f"ll-{tid}",
            total_spend=Decimal("50"),
            last_order_at=lapsed,
        ),
        Customer(
            id=uuid.uuid4(),
            tenant_id=tid,
            name="Active High",
            external_id=f"ah-{tid}",
            total_spend=Decimal("8000"),
            last_order_at=recent,
        ),
    ]
    session.add_all(customers)
    await session.flush()
    return tid, customers


async def test_winback_creates_segment_and_draft(db_session) -> None:
    tid, _ = await _setup(db_session)
    payload = WinBackRequest(
        name="Win back all",
        channel="email",
        message_template="Hey {{first_name}}, come back!",
    )
    campaign = await create_winback_campaign(db_session, tid, payload)

    assert campaign.tenant_id == tid
    assert campaign.status == "draft"
    assert campaign.audience_size == 2
    assert campaign.segment_id is not None


async def test_winback_high_tier_filters_by_spend(db_session) -> None:
    tid, _ = await _setup(db_session)
    payload = WinBackRequest(
        name="Win back high",
        channel="sms",
        message_template="Hey {{first_name}}",
        tier="high",
    )
    campaign = await create_winback_campaign(db_session, tid, payload)

    assert campaign.audience_size == 1
    assert campaign.name == "Win back high"


async def test_winback_reuses_segment_on_repeat(db_session) -> None:
    tid, _ = await _setup(db_session)
    payload = WinBackRequest(
        name="WB1",
        channel="email",
        message_template="hi",
    )
    c1 = await create_winback_campaign(db_session, tid, payload)
    payload2 = WinBackRequest(
        name="WB2",
        channel="email",
        message_template="hi again",
    )
    c2 = await create_winback_campaign(db_session, tid, payload2)

    assert c1.segment_id == c2.segment_id


async def test_winback_tenant_isolation(db_session) -> None:
    tid, _ = await _setup(db_session)
    other_tid = uuid.uuid4()
    db_session.add(Tenant(id=other_tid, name="other"))
    await db_session.flush()

    payload = WinBackRequest(
        name="Other tenant",
        channel="email",
        message_template="hi",
    )
    campaign = await create_winback_campaign(db_session, other_tid, payload)
    assert campaign.audience_size == 0
