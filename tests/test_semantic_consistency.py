"""The semantic config, the Python code and the real database must agree.

A config that describes the database incorrectly is worse than no config, so
every declaration is checked against something independent:

  * declared view / table columns   -> PRAGMA against a real built database
  * declared join keys              -> PRAGMA foreign_key_list
  * declared status filter          -> the statuses actually present in the view
  * declared allowed values         -> the enums that generate the DDL
  * declared aggregation            -> the SQL the operation registry renders
  * the whole thing                 -> a reference query written by hand against
                                       the base tables, bypassing the views
"""

from __future__ import annotations

import sqlite3

import pytest

from eda.data.schema import EXPECTED_TABLES, EXPECTED_VIEWS
from eda.db import fetch_all, fetch_one
from eda.domain.enums import (
    CATEGORIES,
    CHANNELS,
    EXCLUDED_FROM_REVENUE_STATUSES,
    ORDER_STATUSES,
    REGIONS,
    REVENUE_STATUSES,
)
from eda.metrics import (
    AOV_METRIC_ID,
    BREAKDOWN_DIMENSIONS,
    FILTER_DIMENSIONS,
    GMV_METRIC_ID,
    IMPLEMENTED_ANALYSIS_OPERATIONS,
    METRIC_REGISTRY,
    ORDER_COUNT_METRIC_ID,
    SEMANTIC,
    MetricFilters,
    UnsupportedOperationError,
    compute_breakdown,
    compute_core_metrics,
    require_implemented_analysis_operation,
)


# --- config vs. the real database --------------------------------------------


def _actual_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    return [row["name"] for row in fetch_all(conn, f"PRAGMA table_info({name})")]


@pytest.mark.parametrize("view_id", EXPECTED_VIEWS)
def test_declared_view_columns_match_the_database(
    fixture_conn: sqlite3.Connection, view_id: str
) -> None:
    declared = list(SEMANTIC.view(view_id).columns)
    assert declared == _actual_columns(fixture_conn, view_id)


def test_every_view_in_the_database_is_declared() -> None:
    assert set(SEMANTIC.view_ids) == set(EXPECTED_VIEWS)


@pytest.mark.parametrize("table_id", EXPECTED_TABLES)
def test_declared_table_columns_match_the_database(
    fixture_conn: sqlite3.Connection, table_id: str
) -> None:
    declared = list(SEMANTIC.table(table_id).columns)
    assert declared == _actual_columns(fixture_conn, table_id)


def test_every_table_in_the_database_is_declared() -> None:
    assert set(SEMANTIC.table_ids) == set(EXPECTED_TABLES)


@pytest.mark.parametrize("table_id", EXPECTED_TABLES)
def test_declared_primary_keys_match_the_database(
    fixture_conn: sqlite3.Connection, table_id: str
) -> None:
    actual = [
        row["name"]
        for row in fetch_all(fixture_conn, f"PRAGMA table_info({table_id})")
        if row["pk"] > 0
    ]
    assert list(SEMANTIC.table(table_id).primary_key) == actual


def test_declared_relationships_match_the_real_foreign_keys(
    fixture_conn: sqlite3.Connection,
) -> None:
    actual: set[tuple[str, str, str, str]] = set()
    for table_id in EXPECTED_TABLES:
        for row in fetch_all(fixture_conn, f"PRAGMA foreign_key_list({table_id})"):
            actual.add((table_id, row["from"], row["table"], row["to"]))

    declared = {
        (r.from_table, r.from_columns[0], r.to_table, r.to_columns[0])
        for r in SEMANTIC.relationships
    }
    assert declared == actual


@pytest.mark.parametrize("view_id", EXPECTED_VIEWS)
def test_declared_status_filter_matches_what_the_view_returns(
    demo_conn: sqlite3.Connection, view_id: str
) -> None:
    """The demo data contains every status, so the view's filter is observable."""
    present = {
        row["status"] for row in fetch_all(demo_conn, f"SELECT DISTINCT status FROM {view_id}")
    }
    declared = set(SEMANTIC.view(view_id).status_filter)
    if declared:
        assert present == declared
    else:
        assert present == set(ORDER_STATUSES), "an unfiltered view must show every status"


def test_declared_additive_columns_are_numeric(fixture_conn: sqlite3.Connection) -> None:
    for view in SEMANTIC.analysis_views:
        types = {
            row["name"]: row["type"]
            for row in fetch_all(fixture_conn, f"PRAGMA table_info({view.id})")
        }
        for column in view.additive_columns:
            assert types[column].upper() in {"INT", "INTEGER", "NUM", ""}, (
                f"{view.id}.{column} is declared additive but has type {types[column]!r}"
            )


# --- config vs. the enums that generate the DDL ------------------------------


@pytest.mark.parametrize(
    ("dimension_id", "enum_values"),
    [("region", REGIONS), ("category", CATEGORIES), ("channel", CHANNELS)],
)
def test_declared_allowed_values_match_the_domain_enums(
    dimension_id: str, enum_values: tuple[str, ...]
) -> None:
    declared = SEMANTIC.dimension(dimension_id).allowed_values
    assert declared is not None
    assert set(declared) == set(enum_values)


def test_declared_statuses_match_the_domain_enums() -> None:
    for definition in METRIC_REGISTRY.values():
        assert set(definition.status_include) == set(REVENUE_STATUSES), definition.key
        assert set(definition.status_exclude) == set(
            EXCLUDED_FROM_REVENUE_STATUSES
        ), definition.key


# --- config vs. the code that consumes it ------------------------------------


def test_group_by_dimensions_are_exactly_the_breakdown_allow_list() -> None:
    declared = {d.id for d in SEMANTIC.dimensions_supporting("group_by")}
    assert declared == set(BREAKDOWN_DIMENSIONS)


def test_declared_source_fields_are_the_columns_used_for_grouping() -> None:
    for dimension_id, column in BREAKDOWN_DIMENSIONS.items():
        assert SEMANTIC.dimension(dimension_id).source_field == column


def test_filter_dimensions_are_exactly_the_metric_filter_fields() -> None:
    filter_fields = set(MetricFilters.model_fields) - {"start_date", "end_date"}
    assert set(FILTER_DIMENSIONS) == filter_fields


def test_metric_registry_keys_are_the_semantic_metric_ids() -> None:
    assert set(METRIC_REGISTRY) == set(SEMANTIC.metric_ids)
    for key, definition in METRIC_REGISTRY.items():
        assert definition.key == key
        assert definition.spec.id == key


@pytest.mark.parametrize("metric_id", sorted(METRIC_REGISTRY))
def test_rendered_sql_matches_the_declared_aggregation(metric_id: str) -> None:
    definition = METRIC_REGISTRY[metric_id]
    aggregation = definition.spec.aggregation
    sql = definition.sql_expression

    if aggregation.operation == "sum":
        assert sql == f"COALESCE(SUM({aggregation.input_fields[0]}), 0)"
    elif aggregation.operation == "count_distinct":
        assert sql == f"COUNT(DISTINCT {aggregation.input_fields[0]})"
    else:
        numerator = METRIC_REGISTRY[aggregation.numerator_metric].sql_expression
        denominator = METRIC_REGISTRY[aggregation.denominator_metric].sql_expression
        assert sql.startswith(f"CASE WHEN {denominator} = 0 THEN NULL")
        assert f"1.0 * {numerator} / {denominator}" in sql


def test_metric_base_view_is_the_view_the_sql_actually_queries() -> None:
    filters = MetricFilters(start_date="2024-01-01", end_date="2024-12-31")
    from eda.metrics import build_core_metrics_sql

    sql, _ = build_core_metrics_sql(filters)
    for metric_id in (GMV_METRIC_ID, ORDER_COUNT_METRIC_ID):
        assert f"FROM {METRIC_REGISTRY[metric_id].base_view}" in sql


def test_unimplemented_analysis_operations_are_refused_not_faked() -> None:
    """'compare' / 'contribution' are declared 待实现; asking for them must fail."""
    spec = METRIC_REGISTRY[GMV_METRIC_ID].spec
    pending = set(spec.supported_operations) - IMPLEMENTED_ANALYSIS_OPERATIONS
    assert pending, "the config should declare operations that are not built yet"
    for operation in sorted(pending):
        with pytest.raises(UnsupportedOperationError, match="not implemented"):
            require_implemented_analysis_operation(spec, operation)


def test_a_metric_can_refuse_an_operation_it_does_not_support() -> None:
    spec = METRIC_REGISTRY[AOV_METRIC_ID].spec
    assert "contribution" not in spec.supported_operations
    with pytest.raises(UnsupportedOperationError):
        require_implemented_analysis_operation(spec, "contribution")


def test_implemented_operations_are_reported_honestly() -> None:
    for definition in METRIC_REGISTRY.values():
        assert set(definition.implemented_operations) <= set(definition.supported_operations)
        assert set(definition.implemented_operations) <= IMPLEMENTED_ANALYSIS_OPERATIONS
        overlap = set(definition.implemented_operations) & set(definition.pending_operations)
        assert overlap == set()


# --- the executed 口径 vs. an independent reference query ---------------------


def _reference_totals(
    conn: sqlite3.Connection, filters: MetricFilters
) -> tuple[int, int, int, int]:
    """Recompute the metrics with SQL written by hand against the base tables.

    Deliberately does not touch the analysis views, so a mistake inside a view
    definition cannot hide from this test. The status list comes from the
    semantic config and is bound as parameters.
    """
    statuses = METRIC_REGISTRY[GMV_METRIC_ID].status_include
    placeholders = ", ".join(f":status_{index}" for index in range(len(statuses)))
    params: dict[str, object] = {
        f"status_{index}": status for index, status in enumerate(statuses)
    }
    params.update(filters.as_params())

    row = fetch_one(
        conn,
        f"""
        SELECT COALESCE(SUM(i.quantity * i.unit_price_cents), 0) AS gmv_cents,
               COUNT(DISTINCT o.order_id)                        AS orders,
               COALESCE(SUM(i.quantity), 0)                      AS quantity,
               COUNT(DISTINCT o.customer_id)                     AS customers
        FROM orders AS o
        JOIN order_items AS i ON i.order_id = o.order_id
        JOIN products AS p ON p.product_id = i.product_id
        WHERE o.status IN ({placeholders})
          AND o.order_date >= :start_date
          AND o.order_date <= :end_date
          AND (:region IS NULL OR o.order_region = :region)
          AND (:category IS NULL OR p.category = :category)
        """,
        params,
    )
    assert row is not None
    return (row["gmv_cents"], row["orders"], row["quantity"], row["customers"])


_FILTER_CASES = [
    MetricFilters(start_date="2024-01-01", end_date="2024-12-31"),
    MetricFilters(start_date="2024-02-01", end_date="2024-02-29"),
    MetricFilters(start_date="2024-01-01", end_date="2024-12-31", region="华东"),
    MetricFilters(start_date="2024-01-01", end_date="2024-12-31", category="食品生鲜"),
    MetricFilters(start_date="2023-01-01", end_date="2023-12-31"),
]


@pytest.mark.parametrize("filters", _FILTER_CASES, ids=lambda f: f.describe_zh())
def test_semantic_driven_metrics_match_the_reference_query_on_the_fixture(
    fixture_conn: sqlite3.Connection, filters: MetricFilters
) -> None:
    metrics = compute_core_metrics(fixture_conn, filters)
    assert (
        metrics.effective_order_gmv_cents,
        metrics.valid_order_count,
        metrics.item_quantity,
        metrics.distinct_customers,
    ) == _reference_totals(fixture_conn, filters)


@pytest.mark.parametrize("filters", _FILTER_CASES, ids=lambda f: f.describe_zh())
def test_semantic_driven_metrics_match_the_reference_query_on_the_demo_data(
    demo_conn: sqlite3.Connection, filters: MetricFilters
) -> None:
    metrics = compute_core_metrics(demo_conn, filters)
    assert (
        metrics.effective_order_gmv_cents,
        metrics.valid_order_count,
        metrics.item_quantity,
        metrics.distinct_customers,
    ) == _reference_totals(demo_conn, filters)


def test_region_metrics_would_differ_if_signup_region_were_used(
    demo_conn: sqlite3.Connection,
) -> None:
    """Proof that the order_region rule in the config is load-bearing."""
    filters = MetricFilters(start_date="2024-01-01", end_date="2024-12-31", region="华东")
    by_order_region = compute_core_metrics(demo_conn, filters).effective_order_gmv_cents

    row = fetch_one(
        demo_conn,
        "SELECT COALESCE(SUM(l.line_amount_cents), 0) AS gmv "
        "FROM v_revenue_lines AS l "
        "JOIN customers AS c ON c.customer_id = l.customer_id "
        "WHERE c.signup_region = :region "
        "  AND l.order_date >= :start_date AND l.order_date <= :end_date",
        filters.as_params(),
    )
    assert row is not None
    assert by_order_region != row["gmv"], (
        "order_region and signup_region should disagree in the demo data; "
        "otherwise this rule cannot be validated"
    )


# --- date range inclusion rule -----------------------------------------------


def test_date_range_includes_both_endpoints(fixture_conn: sqlite3.Connection) -> None:
    """The fixture's first revenue order is dated 2024-01-05."""
    single_day = MetricFilters(start_date="2024-01-05", end_date="2024-01-05")
    assert compute_core_metrics(fixture_conn, single_day).valid_order_count == 1

    day_after = MetricFilters(start_date="2024-01-06", end_date="2024-01-06")
    assert compute_core_metrics(fixture_conn, day_after).valid_order_count == 0

    for definition in METRIC_REGISTRY.values():
        assert definition.spec.date_range_inclusive == "both"


def test_date_breakdown_only_returns_days_that_have_orders(
    demo_conn: sqlite3.Connection,
) -> None:
    """Full-year demo data proves coverage of the range, not of every single day.

    A by-date breakdown returns exactly the days that have revenue orders; it
    does not zero-fill missing days, and nothing in this project claims that
    every calendar day is populated.
    """
    filters = MetricFilters(start_date="2024-01-01", end_date="2024-12-31")
    breakdown = compute_breakdown(demo_conn, "date", filters)

    row = fetch_one(
        demo_conn,
        "SELECT COUNT(DISTINCT order_date) AS days FROM v_revenue_lines "
        "WHERE order_date >= :start_date AND order_date <= :end_date",
        filters.as_params(),
    )
    assert row is not None
    assert len(breakdown.rows) == row["days"]
    assert all(r.valid_order_count > 0 for r in breakdown.rows)
    assert SEMANTIC.dimension("date").allowed_operations == ("group_by",)
