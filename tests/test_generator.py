"""The demo data must be reproducible and independent of the machine clock."""

from __future__ import annotations

import ast
import datetime as dt
import inspect
import sqlite3

import pytest

from eda.data import generator
from eda.data.generator import DemoDataSpec, generate_demo_dataset
from eda.db import fetch_all, fetch_one
from eda.domain.enums import CATEGORIES, ORDER_STATUSES, REGIONS
from eda.domain.models import Dataset

SMALL_SPEC = DemoDataSpec(n_orders=300, n_customers=60)


def _dump(dataset: Dataset) -> list[dict[str, object]]:
    return [
        record.model_dump(mode="json")
        for group in ("customers", "products", "orders", "order_items")
        for record in getattr(dataset, group)
    ]


def test_same_spec_produces_identical_data() -> None:
    assert _dump(generate_demo_dataset(SMALL_SPEC)) == _dump(generate_demo_dataset(SMALL_SPEC))


def test_different_seed_produces_different_data() -> None:
    other = SMALL_SPEC.model_copy(update={"seed": SMALL_SPEC.seed + 1})
    assert _dump(generate_demo_dataset(SMALL_SPEC)) != _dump(generate_demo_dataset(other))


def test_generator_never_reads_the_system_clock() -> None:
    """A rebuild next year must produce the same rows, so no today()/now() allowed.

    Checked on the parsed syntax tree rather than the raw text, so prose in a
    docstring that merely mentions ``date.today()`` does not trip the check.
    """
    tree = ast.parse(inspect.getsource(generator))
    forbidden_attrs = {"today", "now", "utcnow", "time", "monotonic"}
    offenders = [
        f"line {node.lineno}: .{node.func.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden_attrs
    ]
    assert offenders == [], f"generator must not read the clock: {offenders}"


def test_order_dates_stay_inside_the_configured_window() -> None:
    dataset = generate_demo_dataset(SMALL_SPEC)
    dates = [dt.date.fromisoformat(order.order_date) for order in dataset.orders]
    assert min(dates) >= SMALL_SPEC.start
    assert max(dates) <= SMALL_SPEC.end


def test_custom_window_is_respected() -> None:
    spec = DemoDataSpec(
        seed=7, start_date="2022-03-01", end_date="2022-04-30", n_orders=120, n_customers=25
    )
    dataset = generate_demo_dataset(spec)
    months = {order.order_date[:7] for order in dataset.orders}
    assert months <= {"2022-03", "2022-04"}


def test_start_after_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="start_date must not be after end_date"):
        DemoDataSpec(start_date="2024-12-31", end_date="2024-01-01")


def test_order_ids_follow_the_date_order(demo_dataset: Dataset) -> None:
    dates = [order.order_date for order in demo_dataset.orders]
    assert dates == sorted(dates)
    assert [order.order_id for order in demo_dataset.orders] == sorted(
        order.order_id for order in demo_dataset.orders
    )


def test_demo_data_covers_every_month_region_and_category(demo_dataset: Dataset) -> None:
    months = {order.order_date[:7] for order in demo_dataset.orders}
    assert len(months) == 12, f"expected all 12 months of 2024, got {sorted(months)}"

    assert {order.order_region for order in demo_dataset.orders} == set(REGIONS)
    assert {product.category for product in demo_dataset.products} == set(CATEGORIES)
    assert {order.status for order in demo_dataset.orders} == set(ORDER_STATUSES)


def test_generated_amounts_are_positive_integers(demo_dataset: Dataset) -> None:
    for item in demo_dataset.order_items:
        assert isinstance(item.quantity, int) and item.quantity > 0
        assert isinstance(item.unit_price_cents, int) and item.unit_price_cents > 0
        assert item.line_amount_cents == item.quantity * item.unit_price_cents


def test_transaction_price_never_exceeds_list_price(demo_dataset: Dataset) -> None:
    list_prices = {p.product_id: p.list_price_cents for p in demo_dataset.products}
    for item in demo_dataset.order_items:
        assert item.unit_price_cents <= list_prices[item.product_id]


def test_every_order_has_at_least_one_line(demo_dataset: Dataset) -> None:
    order_ids = {order.order_id for order in demo_dataset.orders}
    assert order_ids == {item.order_id for item in demo_dataset.order_items}


def test_order_region_sometimes_differs_from_signup_region(demo_dataset: Dataset) -> None:
    """Regional reports must use orders.order_region; prove the two really differ."""
    signup = {c.customer_id: c.signup_region for c in demo_dataset.customers}
    moved = [o for o in demo_dataset.orders if o.order_region != signup[o.customer_id]]
    assert moved, "generator should produce orders placed outside the signup region"


def test_dataset_validation_rejects_broken_references(demo_dataset: Dataset) -> None:
    with pytest.raises(ValueError, match="unknown customer"):
        Dataset(
            name="broken",
            customers=demo_dataset.customers[:1],
            products=demo_dataset.products[:1],
            orders=(
                demo_dataset.orders[0].model_copy(update={"customer_id": "C999999"}),
            ),
            order_items=demo_dataset.order_items[:1],
        )


def test_demo_database_has_revenue_and_excluded_orders(demo_conn: sqlite3.Connection) -> None:
    """Sanity check on the built demo DB: both branches of the status filter matter."""
    row = fetch_one(
        demo_conn,
        "SELECT (SELECT COUNT(*) FROM v_revenue_lines) AS revenue_lines, "
        "       (SELECT COUNT(*) FROM v_order_lines) AS all_lines",
    )
    assert row is not None
    assert 0 < row["revenue_lines"] < row["all_lines"]

    statuses = {
        r["status"]
        for r in fetch_all(demo_conn, "SELECT DISTINCT status FROM v_revenue_lines")
    }
    assert statuses == {"paid", "completed"}
