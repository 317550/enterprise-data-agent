"""Metric 口径 tests against the hand-checkable fixture dataset.

Every number asserted here was computed by hand first (see docs/metrics.md) and
stored in tests/data/expected_fixture_metrics.json. The code is compared to the
hand calculation, never the other way round.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from eda.db import fetch_all, fetch_one
from eda.metrics import MetricFilters, compute_breakdown, compute_core_metrics
from eda.metrics.core import AOV_UNDEFINED_NOTE


def _scenario(expected: dict[str, Any], name: str) -> dict[str, Any]:
    for scenario in expected["scenarios"]:
        if scenario["name"] == name:
            return scenario
    raise AssertionError(f"scenario {name!r} missing from the expectation file")


def _scenario_names(expected: dict[str, Any]) -> list[str]:
    return [scenario["name"] for scenario in expected["scenarios"]]


def test_fixture_row_counts(fixture_dataset, expected_fixture_metrics) -> None:
    assert fixture_dataset.row_counts == expected_fixture_metrics["dataset_row_counts"]


def test_every_scenario_matches_the_hand_calculation(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    for name in _scenario_names(expected_fixture_metrics):
        scenario = _scenario(expected_fixture_metrics, name)
        metrics = compute_core_metrics(
            fixture_conn, MetricFilters(**scenario["filters"])
        )
        assert metrics.revenue_cents == scenario["revenue_cents"], name
        assert metrics.valid_order_count == scenario["valid_order_count"], name
        assert metrics.item_quantity == scenario["item_quantity"], name
        assert metrics.distinct_customers == scenario["distinct_customers"], name


def test_aov_equals_revenue_over_distinct_orders(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    for name in _scenario_names(expected_fixture_metrics):
        scenario = _scenario(expected_fixture_metrics, name)
        metrics = compute_core_metrics(fixture_conn, MetricFilters(**scenario["filters"]))
        orders = scenario["valid_order_count"]
        if orders == 0:
            assert metrics.aov_cents is None, name
            continue
        assert metrics.aov_cents == pytest.approx(scenario["revenue_cents"] / orders), name
        if scenario["exact_aov_cents"] is not None:
            assert metrics.aov_cents == float(scenario["exact_aov_cents"]), name


def test_zero_denominator_returns_none_with_an_explanation(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    scenario = _scenario(expected_fixture_metrics, "empty_window_2023")
    metrics = compute_core_metrics(fixture_conn, MetricFilters(**scenario["filters"]))

    assert metrics.valid_order_count == 0
    assert metrics.aov_cents is None
    assert metrics.aov_yuan is None
    assert AOV_UNDEFINED_NOTE in metrics.notes
    assert "无定义" in metrics.summary_zh()


def test_cancelled_pending_and_refunded_orders_are_excluded(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    excluded_ids = {row["order_id"] for row in expected_fixture_metrics["excluded_orders"]}
    present = {
        row["order_id"]
        for row in fetch_all(fixture_conn, "SELECT DISTINCT order_id FROM v_revenue_lines")
    }
    assert excluded_ids & present == set()

    # ... and they do exist in the raw tables, so the exclusion is real filtering
    # rather than the rows simply being absent from the dataset.
    for row in expected_fixture_metrics["excluded_orders"]:
        raw = fetch_one(
            fixture_conn,
            "SELECT o.status, SUM(i.quantity * i.unit_price_cents) AS amount "
            "FROM orders AS o JOIN order_items AS i ON i.order_id = o.order_id "
            "WHERE o.order_id = :order_id GROUP BY o.status",
            {"order_id": row["order_id"]},
        )
        assert raw is not None, row["order_id"]
        assert raw["status"] == row["status"]
        assert raw["amount"] == row["line_amount_cents"]


def test_excluded_amount_accounts_for_the_full_dataset(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    """Revenue + excluded = all statuses. No amount is silently lost."""
    all_status = fetch_one(
        fixture_conn, "SELECT SUM(line_amount_cents) AS total FROM v_order_lines"
    )
    assert all_status is not None
    assert all_status["total"] == expected_fixture_metrics["all_status_line_amount_cents"]

    full = _scenario(expected_fixture_metrics, "full_window")
    excluded = sum(row["line_amount_cents"] for row in expected_fixture_metrics["excluded_orders"])
    assert full["revenue_cents"] + excluded == all_status["total"]


def test_orders_are_counted_once_regardless_of_line_count(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    """COUNT(DISTINCT order_id) vs COUNT(*): the classic double-counting trap."""
    full = _scenario(expected_fixture_metrics, "full_window")
    row = fetch_one(
        fixture_conn,
        "SELECT COUNT(*) AS lines, COUNT(DISTINCT order_id) AS orders FROM v_revenue_lines",
    )
    assert row is not None
    assert row["lines"] == expected_fixture_metrics["revenue_line_count"]
    assert row["orders"] == full["valid_order_count"]
    assert row["lines"] > row["orders"], "fixture must contain a multi-line order"

    # The multi-line order (3 lines) still contributes exactly one order.
    multi = fetch_all(
        fixture_conn,
        "SELECT order_id, line_count FROM v_revenue_orders WHERE line_count > 1",
    )
    assert multi, "fixture must contain a multi-line revenue order"


def test_order_grain_view_matches_line_grain_totals(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    """Aggregating at order grain and at line grain must agree, to the cent."""
    full = _scenario(expected_fixture_metrics, "full_window")
    row = fetch_one(
        fixture_conn,
        "SELECT SUM(order_amount_cents) AS revenue, COUNT(*) AS orders FROM v_revenue_orders",
    )
    assert row is not None
    assert row["revenue"] == full["revenue_cents"]
    assert row["orders"] == full["valid_order_count"]

    per_order = {
        r["order_id"]: r["order_amount_cents"]
        for r in fetch_all(
            fixture_conn, "SELECT order_id, order_amount_cents FROM v_revenue_orders"
        )
    }
    assert per_order == expected_fixture_metrics["revenue_order_amounts_cents"]


def test_joining_products_does_not_duplicate_amounts(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    """products joins 1:1 on its primary key, so the total must not move."""
    full = _scenario(expected_fixture_metrics, "full_window")
    row = fetch_one(
        fixture_conn,
        "SELECT SUM(l.line_amount_cents) AS revenue "
        "FROM v_revenue_lines AS l JOIN products AS p ON p.product_id = l.product_id",
    )
    assert row is not None
    assert row["revenue"] == full["revenue_cents"]


def test_joining_customers_does_not_duplicate_amounts(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    full = _scenario(expected_fixture_metrics, "full_window")
    row = fetch_one(
        fixture_conn,
        "SELECT SUM(l.line_amount_cents) AS revenue, COUNT(DISTINCT l.order_id) AS orders "
        "FROM v_revenue_lines AS l JOIN customers AS c ON c.customer_id = l.customer_id",
    )
    assert row is not None
    assert row["revenue"] == full["revenue_cents"]
    assert row["orders"] == full["valid_order_count"]


@pytest.mark.parametrize("dimension", ["month", "region", "category", "channel"])
def test_breakdowns_match_the_hand_calculation(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics, dimension: str
) -> None:
    filters = MetricFilters(**_scenario(expected_fixture_metrics, "full_window")["filters"])
    breakdown = compute_breakdown(fixture_conn, dimension, filters)

    actual = {
        row.dimension_value: (row.revenue_cents, row.valid_order_count)
        for row in breakdown.rows
    }
    expected = {
        row["dimension_value"]: (row["revenue_cents"], row["valid_order_count"])
        for row in expected_fixture_metrics["breakdowns"][dimension]
    }
    assert actual == expected


@pytest.mark.parametrize("dimension", ["month", "region", "channel"])
def test_order_disjoint_breakdowns_sum_to_the_total(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics, dimension: str
) -> None:
    """Month / region / channel partition the orders, so both totals must add up."""
    full = _scenario(expected_fixture_metrics, "full_window")
    filters = MetricFilters(**full["filters"])
    breakdown = compute_breakdown(fixture_conn, dimension, filters)

    assert breakdown.total_revenue_cents == full["revenue_cents"]
    assert (
        sum(row.valid_order_count for row in breakdown.rows) == full["valid_order_count"]
    )


def test_category_breakdown_warns_that_order_counts_overlap(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    """Revenue still adds up by category, but order counts deliberately do not."""
    full = _scenario(expected_fixture_metrics, "full_window")
    filters = MetricFilters(**full["filters"])
    breakdown = compute_breakdown(fixture_conn, "category", filters)

    assert breakdown.total_revenue_cents == full["revenue_cents"]
    order_sum = sum(row.valid_order_count for row in breakdown.rows)
    assert order_sum == expected_fixture_metrics["category_order_count_sum"]
    assert order_sum > full["valid_order_count"]
    assert any("订单数相加会大于总订单数" in note for note in breakdown.notes)


def test_breakdown_rejects_unknown_dimension(
    fixture_conn: sqlite3.Connection, expected_fixture_metrics
) -> None:
    filters = MetricFilters(**_scenario(expected_fixture_metrics, "full_window")["filters"])
    with pytest.raises(ValueError, match="unknown breakdown dimension"):
        compute_breakdown(fixture_conn, "order_region; DROP TABLE orders --", filters)


def test_revenue_uses_transaction_price_not_list_price(
    fixture_conn: sqlite3.Connection,
) -> None:
    """The fixture contains discounted lines; using list_price would inflate revenue."""
    row = fetch_one(
        fixture_conn,
        "SELECT SUM(l.line_amount_cents) AS transacted, "
        "       SUM(l.quantity * p.list_price_cents) AS at_list_price "
        "FROM v_revenue_lines AS l JOIN products AS p ON p.product_id = l.product_id",
    )
    assert row is not None
    assert row["transacted"] < row["at_list_price"]


def test_money_columns_are_integers(fixture_conn: sqlite3.Connection) -> None:
    non_integer = fetch_all(
        fixture_conn,
        "SELECT order_item_id FROM order_items "
        "WHERE typeof(unit_price_cents) <> 'integer' OR typeof(quantity) <> 'integer'",
    )
    assert non_integer == []
