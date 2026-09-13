"""AnalysisPlan pipeline: compile → validate → execute → structured result."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eda.metrics import MetricFilters, compute_breakdown, compute_core_metrics
from eda.query.errors import QueryError
from eda.query.service import run_analysis_plan
from eda.sql.executor import ExecutionLimits


def _scenario(expected: dict[str, Any], name: str) -> dict[str, Any]:
    for scenario in expected["scenarios"]:
        if scenario["name"] == name:
            return scenario
    raise AssertionError(f"scenario {name!r} missing from the expectation file")


def _limits(**overrides) -> ExecutionLimits:
    payload = {
        "max_sql_chars": 8000,
        "max_rows": 1000,
        "max_result_bytes": 256000,
        "timeout_seconds": 10.0,
        "max_value_bytes": 4096,
    }
    payload.update(overrides)
    return ExecutionLimits(**payload)


def test_total_gmv_matches_fixture_and_existing_metrics(
    fixture_db: Path, fixture_conn, expected_fixture_metrics
) -> None:
    scenario = _scenario(expected_fixture_metrics, "full_window")
    filters = MetricFilters(**scenario["filters"])
    existing = compute_core_metrics(fixture_conn, filters)
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "total",
            "start_date": scenario["filters"]["start_date"],
            "end_date": scenario["filters"]["end_date"],
        },
        fixture_db,
    )
    assert result.rows[0].metric_value == scenario["revenue_cents"]
    assert result.rows[0].metric_value == existing.revenue_cents
    assert result.completeness.is_complete_population is True
    assert result.completeness.reaggregation_safe is True
    assert result.completeness.truncated is False
    assert result.execution.sql.count("v_revenue_lines") == 1


def test_orders_are_counted_once_regardless_of_line_count(
    fixture_db: Path, expected_fixture_metrics
) -> None:
    scenario = _scenario(expected_fixture_metrics, "full_window")
    result = run_analysis_plan(
        {
            "metric_id": "valid_order_count",
            "operation": "total",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
        fixture_db,
    )
    assert result.rows[0].metric_value == scenario["valid_order_count"]
    assert result.rows[0].metric_value != expected_fixture_metrics["revenue_line_count"]


def test_region_filter_matches_hand_calculation(
    fixture_db: Path, expected_fixture_metrics
) -> None:
    scenario = _scenario(expected_fixture_metrics, "region_east")
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "total",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "filters": [{"dimension_id": "region", "op": "eq", "value": "华东"}],
        },
        fixture_db,
    )
    assert result.rows[0].metric_value == scenario["revenue_cents"]


def test_aov_and_zero_denominator(fixture_db: Path, expected_fixture_metrics) -> None:
    full = _scenario(expected_fixture_metrics, "full_window")
    aov = run_analysis_plan(
        {
            "metric_id": "aov_cents",
            "operation": "total",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
        fixture_db,
    )
    assert aov.rows[0].metric_value == float(full["exact_aov_cents"])
    assert aov.display_metric_name_zh == "客单价"

    empty = run_analysis_plan(
        {
            "metric_id": "aov_cents",
            "operation": "total",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
        fixture_db,
    )
    assert empty.rows[0].metric_value is None
    assert any("无定义" in note for note in empty.warnings)


def test_empty_breakdown_is_success_with_a_warning(fixture_db: Path) -> None:
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
            "filters": [{"dimension_id": "region", "op": "eq", "value": "华中"}],
        },
        fixture_db,
    )
    assert result.rows == ()
    assert result.completeness.is_complete_population is True
    assert any("没有" in note for note in result.warnings)


def test_category_gmv_ranking_matches_hand_calculation(
    fixture_db: Path, fixture_conn, expected_fixture_metrics
) -> None:
    expected = expected_fixture_metrics["breakdowns"]["category"]
    ranked = sorted(expected, key=lambda row: (-row["revenue_cents"], row["dimension_value"]))
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        },
        fixture_db,
    )
    assert [row.dimension_value for row in result.rows] == [
        row["dimension_value"] for row in ranked
    ]
    assert [row.metric_value for row in result.rows] == [
        row["revenue_cents"] for row in ranked
    ]
    existing = compute_breakdown(
        fixture_conn, "category", MetricFilters(start_date="2024-01-01", end_date="2024-12-31")
    )
    assert [row.dimension_value for row in result.rows] == [
        row.dimension_value for row in existing.rows
    ]


def test_category_aov_uses_contribution_display_name(fixture_db: Path) -> None:
    result = run_analysis_plan(
        {
            "metric_id": "aov_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        },
        fixture_db,
    )
    assert result.display_metric_name_zh == "订单平均贡献额"
    assert result.metric_name_zh == "客单价"


def test_tied_order_counts_are_sorted_stably(fixture_db: Path) -> None:
    first = run_analysis_plan(
        {
            "metric_id": "valid_order_count",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        },
        fixture_db,
    )
    second = run_analysis_plan(
        {
            "metric_id": "valid_order_count",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        },
        fixture_db,
    )
    assert [row.dimension_value for row in first.rows] == [
        row.dimension_value for row in second.rows
    ]
    values = [row.metric_value for row in first.rows]
    assert values == sorted(values, reverse=True)
    tied = [
        row.dimension_value
        for row in first.rows
        if row.metric_value == first.rows[0].metric_value
    ]
    assert tied == sorted(tied)
    assert any("横跨" in note for note in first.warnings)


def test_top_n_is_not_a_complete_population(fixture_db: Path) -> None:
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
            "top_n": 2,
        },
        fixture_db,
    )
    assert len(result.rows) == 2
    assert result.completeness.ranked_top_n == 2
    assert result.completeness.is_complete_population is False
    assert result.completeness.reaggregation_safe is False
    assert result.completeness.truncated is False
    assert "前 2 项" in result.completeness.note_zh


def test_executor_truncation_is_distinct_from_top_n(fixture_db: Path) -> None:
    result = run_analysis_plan(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        },
        fixture_db,
        limits=_limits(max_rows=2),
    )
    assert len(result.rows) == 2
    assert result.completeness.ranked_top_n is None
    assert result.completeness.truncated is True
    assert result.completeness.truncation_reason == "max_rows"
    assert result.completeness.is_complete_population is False
    assert "截断" in result.completeness.note_zh


def test_invalid_plan_does_not_reach_the_database(fixture_db: Path) -> None:
    with pytest.raises(QueryError) as exc:
        run_analysis_plan(
            {
                "metric_id": "effective_order_gmv_cents",
                "operation": "total",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "sql": "SELECT 1",
            },
            fixture_db,
        )
    assert exc.value.code == "invalid_plan"
