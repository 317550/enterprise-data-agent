"""Offline period/protocol/capability boundaries for stage 4B-1."""

import pytest
from pydantic import ValidationError

from eda.plan.comparative import (
    ComparativeAnalysisPlan, ComparativeFilter, PeriodSpec, month_period,
    parse_comparative_plan, previous_month, validate_as_of,
)
from eda.semantic import load_semantic_model
from eda.semantic.models import SemanticModel

GMV = "effective_order_gmv_cents"


def payload(**updates):
    raw = dict(metric_id=GMV, operation="compare", current_period=month_period(2024, 2),
               baseline_period=month_period(2024, 1))
    raw.update(updates)
    return raw


@pytest.mark.parametrize("year,month,end", [(2024, 2, "2024-02-29"), (2023, 2, "2023-02-28"),
                                          (2000, 2, "2000-02-29"), (1900, 2, "1900-02-28"),
                                          (2024, 4, "2024-04-30"), (2024, 12, "2024-12-31")])
def test_complete_month(year, month, end):
    assert month_period(year, month).end_date == end


@pytest.mark.parametrize("year", [2023, 2024])
def test_complete_year(year):
    period = PeriodSpec(start_date=f"{year}-01-01", end_date=f"{year}-12-31",
                        label=f"{year}年", granularity="year", is_complete=True)
    assert PeriodSpec.model_validate_json(period.model_dump_json()) == period


@pytest.mark.parametrize("updates", [
    {"start_date": "2024-02-02"}, {"end_date": "2024-02-28"},
    {"end_date": "2024-02-30"}, {"start_date": "2024-2-01"},
    {"start_date": "20240201"}, {"start_date": "2024-03-01"},
    {"end_date": "2024-03-31"}, {"start_date": "2024-02-01T00:00:00"},
    {"granularity": "quarter"}, {"granularity": "week"}, {"granularity": "year"},
    {"is_complete": False}, {"is_complete": 1}, {"is_complete": "true"},
    {"start_date": 20240201}, {"sql": "SELECT 1"}, {"label": " "},
])
def test_invalid_period(updates):
    raw = month_period(2024, 2).model_dump()
    raw.update(updates)
    with pytest.raises(ValidationError):
        PeriodSpec.model_validate(raw)


@pytest.mark.parametrize("year,month,baseline_start,baseline_end", [
    (2024, 1, "2023-12-01", "2023-12-31"),
    (2024, 3, "2024-02-01", "2024-02-29"),
    (2023, 3, "2023-02-01", "2023-02-28"),
])
def test_mom_is_derived(year, month, baseline_start, baseline_end):
    plan = parse_comparative_plan(payload(operation="mom", current_period=month_period(year, month),
                                          baseline_period=None))
    assert (plan.baseline_period.start_date, plan.baseline_period.end_date) == (baseline_start, baseline_end)
    assert parse_comparative_plan(plan) == plan


def test_mom_rejects_invented_baseline_and_year():
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(operation="mom", baseline_period=month_period(2023, 12)))
    year = PeriodSpec(start_date="2024-01-01", end_date="2024-12-31", label="2024年",
                      granularity="year", is_complete=True)
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(operation="mom", current_period=year))
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(current_period=year))
    with pytest.raises(ValueError):
        previous_month(month_period(1, 1))


def test_compare_years():
    periods = [PeriodSpec(start_date=f"{y}-01-01", end_date=f"{y}-12-31", label=str(y),
                          granularity="year", is_complete=True) for y in (2023, 2024)]
    assert parse_comparative_plan(payload(current_period=periods[1], baseline_period=periods[0]))


@pytest.mark.parametrize("baseline", [None, month_period(2024, 2), month_period(2024, 3)])
def test_compare_requires_explicit_preceding_nonoverlapping_baseline(baseline):
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(baseline_period=baseline))


def test_reference_date_must_cover_month_end():
    raw = payload(operation="mom")
    with pytest.raises(ValueError, match="not complete"):
        validate_as_of(raw, "2024-02-28")
    assert validate_as_of(raw, "2024-02-29").current_period.is_complete
    with pytest.raises(ValueError):
        validate_as_of(raw, "20240229")


@pytest.mark.parametrize("updates", [
    {"metric_id": "orders"}, {"metric_id": "SUM(amount)"}, {"metric_id": "成交额"},
    {"operation": "total"}, {"operation": "yoy"}, {"dimension_id": "region"},
    {"top_n": 3}, {"sql": "SELECT 1"}, {"table": "orders"}, {"column": "amount"},
    {"expression": "a-b"}, {"formula": "a/b"}, {"dimensions": ["region", "category"]},
    {"sort_direction": "descending"},
])
def test_closed_protocol(updates):
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(**updates))


@pytest.mark.parametrize("metric,dimension,allowed", [
    (GMV, "region", True), (GMV, "category", True), (GMV, "channel", False),
    ("item_quantity", "category", True), ("valid_order_count", "region", True),
    ("valid_order_count", "category", False), ("aov_cents", "region", False),
    ("distinct_customers", "region", False), (GMV, None, False),
    (GMV, ["region", "category"], False),
])
def test_contribution_capabilities(metric, dimension, allowed):
    raw = payload(metric_id=metric, operation="contribution", dimension_id=dimension)
    if allowed:
        assert parse_comparative_plan(raw).dimension_id == dimension
    else:
        with pytest.raises(ValidationError):
            parse_comparative_plan(raw)


@pytest.mark.parametrize("top_n", [True, "3", 0, 101, 2.5])
def test_strict_top_n(top_n):
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(operation="contribution", dimension_id="region", top_n=top_n))


def test_json_and_filters_roundtrip():
    raw = payload(filters=(ComparativeFilter(dimension_id="region", op="in", value=("华东", "华北")),))
    plan = parse_comparative_plan(raw)
    assert ComparativeAnalysisPlan.model_validate_json(plan.model_dump_json()) == plan
    assert parse_comparative_plan(plan) == plan


@pytest.mark.parametrize("clause", [
    dict(dimension_id="region", op="eq", value="SQL"),
    dict(dimension_id="region", op="like", value="华东"),
    dict(dimension_id="orders", op="eq", value="华东"),
    dict(dimension_id="region", op="eq", value=1),
    dict(dimension_id="region", op="in", value=("华东", "华东")),
])
def test_invalid_filters(clause):
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(filters=(clause,)))


@pytest.mark.parametrize("bypass", ["construct", "copy"])
def test_instance_bypass_is_revalidated(bypass):
    period = month_period(2024, 2)
    bad_period = (PeriodSpec.model_construct(**(period.model_dump() | {"end_date": "2024-02-28"}))
                  if bypass == "construct" else period.model_copy(update={"end_date": "2024-02-28"}))
    with pytest.raises(ValidationError):
        PeriodSpec.model_validate(bad_period)
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(current_period=bad_period))
    valid = parse_comparative_plan(payload())
    bad = (ComparativeAnalysisPlan.model_construct(**(valid.model_dump() | {"operation": "sql"}))
           if bypass == "construct" else valid.model_copy(update={"operation": "sql"}))
    with pytest.raises(ValidationError):
        parse_comparative_plan(bad)
    clause = ComparativeFilter.model_construct(dimension_id="region", op="in", value="华东")
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(filters=(clause,)))


def test_model_copy_extra_and_duplicate_filters_are_rejected():
    valid = parse_comparative_plan(payload())
    with pytest.raises(ValidationError):
        parse_comparative_plan(valid.model_copy(update={"sql": "SELECT 1"}))
    clause = ComparativeFilter(dimension_id="region", op="eq", value="华东")
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(filters=(clause, clause)))
    nested = valid.model_copy(update={"current_period": valid.current_period.model_copy(update={"sql": "SELECT 1"})})
    with pytest.raises(ValidationError):
        parse_comparative_plan(nested)
    nested = valid.model_copy(update={"filters": (clause.model_copy(update={"column": "order_region"}),)})
    with pytest.raises(ValidationError):
        parse_comparative_plan(nested)


def test_python_containers_and_filter_values_remain_strict():
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(filters=[]))
    with pytest.raises(ValidationError):
        ComparativeFilter(dimension_id="region", op="in", value=["华东"])


def test_capability_is_driven_by_config(monkeypatch):
    import eda.plan.comparative as module
    raw = load_semantic_model().model_dump()
    raw["metrics"][0]["contribution_dimensions"] = ("region",)
    configured = SemanticModel.model_validate(raw)
    monkeypatch.setattr(module, "load_semantic_model", lambda: configured)
    with pytest.raises(ValidationError):
        parse_comparative_plan(payload(operation="contribution", dimension_id="category"))


@pytest.mark.parametrize("updates", [
    {"contribution_dimensions": ("region", "region")},
    {"contribution_dimensions": ("ghost",)},
    {"additive_dimensions": ()},
    {"contribution_dimensions": ()},
])
def test_inconsistent_capability_config_rejected(updates):
    raw = load_semantic_model().model_dump()
    raw["metrics"][0].update(updates)
    with pytest.raises(ValidationError):
        SemanticModel.model_validate(raw)


def test_conflicting_dimension_additivity_rejected():
    raw = load_semantic_model().model_dump()
    raw["metrics"][1]["additive_dimensions"] += ("category",)
    with pytest.raises(ValidationError, match="conflicting"):
        SemanticModel.model_validate(raw)
