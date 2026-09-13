"""Deterministic compilation: identifiers from config, values as parameters."""

from __future__ import annotations

from eda.plan.models import AnalysisPlan, FilterClause
from eda.sql.compiler import compile_plan


def test_total_sql_is_parameterized_and_stable() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "metric_id": "effective_order_gmv_cents",
            "operation": "total",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "filters": [{"dimension_id": "region", "op": "eq", "value": "华东"}],
        }
    )
    first = compile_plan(plan)
    second = compile_plan(plan)
    assert first.sql == second.sql
    assert first.params == second.params
    assert "华东" not in first.sql
    assert first.params["filter_0"] == "华东"
    assert "v_revenue_lines" in first.sql
    assert "COALESCE(SUM(line_amount_cents), 0)" in first.sql


def test_injection_like_filter_values_stay_in_parameters(monkeypatch) -> None:
    from eda.metrics.definitions import SEMANTIC

    # Test-only controlled vocabulary entry: retain the injection assertion
    # while exercising the public compiler's full plan validation.
    region = SEMANTIC.dimension("region")
    monkeypatch.setitem(region.__dict__, "allowed_values", (*region.allowed_values, "'; DROP TABLE orders --"))
    evil = FilterClause(dimension_id="region", op="eq", value="'; DROP TABLE orders --")
    plan = AnalysisPlan.model_construct(
        metric_id="effective_order_gmv_cents",
        operation="total",
        start_date="2024-01-01",
        end_date="2024-12-31",
        dimension_id=None,
        filters=(evil,),
        order_by=None,
        sort_direction=None,
        top_n=None,
    )
    compiled = compile_plan(plan)
    assert "DROP TABLE" not in compiled.sql
    assert compiled.params["filter_0"] == "'; DROP TABLE orders --"


def test_breakdown_ranks_by_the_requested_metric_not_gmv() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "metric_id": "aov_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "region",
            "order_by": "metric_value",
            "sort_direction": "desc",
            "top_n": 2,
        }
    )
    compiled = compile_plan(plan)
    assert "ORDER BY metric_value DESC NULLS LAST, dimension_value ASC" in compiled.sql
    assert "effective_order_gmv_cents DESC" not in compiled.sql
    assert compiled.params["top_n"] == 2
    assert compiled.display_metric_name_zh == "客单价"


def test_category_aov_uses_contribution_display_name() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "metric_id": "aov_cents",
            "operation": "breakdown",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dimension_id": "category",
        }
    )
    assert compile_plan(plan).display_metric_name_zh == "订单平均贡献额"


def test_region_filter_uses_order_region() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "metric_id": "valid_order_count",
            "operation": "total",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "filters": [{"dimension_id": "region", "op": "eq", "value": "西南"}],
        }
    )
    compiled = compile_plan(plan)
    assert "order_region = :filter_0" in compiled.sql
    assert "signup_region" not in compiled.sql
    assert "COUNT(DISTINCT order_id)" in compiled.sql
