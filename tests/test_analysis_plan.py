"""AnalysisPlan accepts approved requests and rejects illegal field combinations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eda.plan.models import AnalysisPlan, parse_analysis_plan
from eda.query.errors import QueryError


def _total(**overrides):
    payload = {
        "metric_id": "effective_order_gmv_cents",
        "operation": "total",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    }
    payload.update(overrides)
    return payload


def _breakdown(**overrides):
    payload = {
        "metric_id": "effective_order_gmv_cents",
        "operation": "breakdown",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "dimension_id": "category",
    }
    payload.update(overrides)
    return payload


def test_valid_total_and_breakdown_are_accepted() -> None:
    total = AnalysisPlan.model_validate(_total())
    assert total.dimension_id is None
    assert total.top_n is None
    ranked = AnalysisPlan.model_validate(_breakdown(top_n=3, sort_direction="asc"))
    assert ranked.order_by == "metric_value"
    assert ranked.top_n == 3
    assert ranked.sort_direction == "asc"


def test_legacy_metric_id_is_canonicalised() -> None:
    plan = AnalysisPlan.model_validate(_total(metric_id="revenue_cents"))
    assert plan.metric_id == "effective_order_gmv_cents"


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown metric"):
        AnalysisPlan.model_validate(_total(metric_id="net_profit_cents"))


def test_unknown_dimension_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown dimension"):
        AnalysisPlan.model_validate(_breakdown(dimension_id="weather"))


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_total(sql="SELECT 1"))
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_total(table="orders"))
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_total(expression="SUM(x)"))


def test_reversed_dates_are_rejected() -> None:
    with pytest.raises(ValidationError, match="start_date"):
        AnalysisPlan.model_validate(_total(start_date="2024-12-31", end_date="2024-01-01"))


def test_total_rejects_dimension_and_top_n() -> None:
    with pytest.raises(ValidationError, match="dimension_id"):
        AnalysisPlan.model_validate(_total(dimension_id="region"))
    with pytest.raises(ValidationError, match="top_n"):
        AnalysisPlan.model_validate(_total(top_n=5))


def test_breakdown_requires_dimension() -> None:
    with pytest.raises(ValidationError, match="dimension_id"):
        AnalysisPlan.model_validate(
            {
                "metric_id": "effective_order_gmv_cents",
                "operation": "breakdown",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            }
        )


def test_unsupported_operation_is_not_downgraded() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_total(operation="compare"))
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_total(operation="contribution"))


def test_metric_that_does_not_allow_a_dimension_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from eda.metrics.definitions import GMV_METRIC_ID, METRIC_REGISTRY

    original = METRIC_REGISTRY[GMV_METRIC_ID]
    monkeypatch.setitem(
        METRIC_REGISTRY,
        GMV_METRIC_ID,
        original.model_copy(update={"allowed_dimensions": ("region",)}),
    )
    with pytest.raises(ValidationError, match="does not allow dimension"):
        AnalysisPlan.model_validate(_breakdown(dimension_id="category"))
    AnalysisPlan.model_validate(_breakdown(dimension_id="region"))


def test_filter_only_allows_approved_filter_dimensions() -> None:
    ok = AnalysisPlan.model_validate(
        _total(filters=[{"dimension_id": "region", "op": "eq", "value": "华东"}])
    )
    assert ok.filters[0].values() == ("华东",)
    with pytest.raises(ValidationError, match="not approved for filtering"):
        AnalysisPlan.model_validate(
            _total(filters=[{"dimension_id": "month", "op": "eq", "value": "2024-01"}])
        )


def test_in_filter_accepts_a_short_list_of_approved_values() -> None:
    plan = AnalysisPlan.model_validate(
        _total(
            filters=[
                {"dimension_id": "region", "op": "in", "value": ["华东", "华北"]}
            ]
        )
    )
    assert plan.filters[0].values() == ("华东", "华北")


def test_top_n_must_be_a_restricted_positive_int() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_breakdown(top_n=0))
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_breakdown(top_n=10000))


def test_parse_analysis_plan_maps_errors() -> None:
    with pytest.raises(QueryError) as exc:
        parse_analysis_plan(_total(metric_id="nope_metric"))
    assert exc.value.code == "invalid_plan"
