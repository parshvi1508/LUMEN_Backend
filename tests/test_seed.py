from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts.seed import TOTAL_CUSTOMERS, make_customers, make_orders

NOW = datetime(2026, 6, 12, tzinfo=UTC)


def test_customer_counts_and_uniqueness() -> None:
    customers = make_customers()
    assert len(customers) == TOTAL_CUSTOMERS == 600
    assert len({c.external_id for c in customers}) == 600
    cohorts = {c.attributes["cohort"] for c in customers}
    assert cohorts == {"loyalist", "lapsed", "one_time", "regular"}


def test_order_counts_in_range() -> None:
    orders = make_orders(make_customers(), now=NOW)
    assert 2300 <= len(orders) <= 2700
    assert len({o.external_id for o in orders}) == len(orders)


def test_lapsed_have_no_recent_orders() -> None:
    customers = make_customers()
    orders = make_orders(customers, now=NOW)
    lapsed_ids = {c.external_id for c in customers if c.attributes["cohort"] == "lapsed"}
    cutoff = NOW - timedelta(days=90)
    lapsed_orders = [o for o in orders if o.customer_external_id in lapsed_ids]
    assert lapsed_orders
    assert all(o.ordered_at < cutoff for o in lapsed_orders)


def test_one_time_buyers_have_exactly_one_order() -> None:
    customers = make_customers()
    orders = make_orders(customers, now=NOW)
    one_time_ids = {c.external_id for c in customers if c.attributes["cohort"] == "one_time"}
    counts: dict[str, int] = dict.fromkeys(one_time_ids, 0)
    for o in orders:
        if o.customer_external_id in one_time_ids:
            counts[o.customer_external_id] += 1
    assert set(counts.values()) == {1}


def test_amounts_positive_two_decimal_places() -> None:
    orders = make_orders(make_customers(), now=NOW)
    for o in orders:
        assert o.amount > 0
        assert o.amount == o.amount.quantize(Decimal("0.01"))


def test_deterministic_across_runs() -> None:
    a = make_orders(make_customers(), now=NOW)
    b = make_orders(make_customers(), now=NOW)
    assert [(o.external_id, o.amount) for o in a] == [(o.external_id, o.amount) for o in b]
