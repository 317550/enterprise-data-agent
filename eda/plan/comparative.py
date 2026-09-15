"""Closed comparative business protocol; no query or orchestration entry point."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eda.plan.models import FilterClause, MAX_TOP_N
from eda.semantic import load_semantic_model


def _export(value):
    if isinstance(value, BaseModel):
        exported = value.model_dump(mode="python", warnings=False)
        exported.update(value.__dict__)
        exported.update(value.model_extra or {})
        return {key: _export(item) for key, item in exported.items()}
    if isinstance(value, dict):
        return {key: _export(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_export(item) for item in value)
    if isinstance(value, list):
        return [_export(item) for item in value]
    return value


class StrictComparativeModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                              revalidate_instances="always")

    @model_validator(mode="wrap")
    @classmethod
    def reparse_instances(cls, value, handler):
        # Never trust an instance, including model_construct/model_copy products.
        return handler(_export(value))


def iso_date(value: str) -> date:
    if type(value) is not str:
        raise ValueError("dates must use YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("dates must use YYYY-MM-DD")
    return parsed


class PeriodSpec(StrictComparativeModel):
    start_date: str
    end_date: str
    label: str = Field(min_length=1, max_length=80)
    granularity: Literal["month", "year"]
    is_complete: bool

    @model_validator(mode="after")
    def calendar_period(self):
        start, end = iso_date(self.start_date), iso_date(self.end_date)
        if not self.label.strip() or not self.is_complete or start > end:
            raise ValueError("a complete, labelled calendar period is required")
        if self.granularity == "month":
            expected_start = end.replace(day=1)
            expected_end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
        else:
            expected_start = date(end.year, 1, 1)
            expected_end = date(end.year, 12, 31)
        if (start, end) != (expected_start, expected_end):
            raise ValueError("period must cover exactly one complete calendar month/year")
        return self


def month_period(year: int, month: int) -> PeriodSpec:
    start = date(year, month, 1)
    return PeriodSpec(start_date=start.isoformat(),
                      end_date=date(year, month, calendar.monthrange(year, month)[1]).isoformat(),
                      label=f"{year}年{month}月", granularity="month", is_complete=True)


def previous_month(period: PeriodSpec) -> PeriodSpec:
    period = PeriodSpec.model_validate(period)
    if period.granularity != "month":
        raise ValueError("mom requires a calendar month")
    start = iso_date(period.start_date)
    if start == date.min:
        raise ValueError("previous month is outside supported calendar")
    previous = start - timedelta(days=1)
    return month_period(previous.year, previous.month)


class ComparativeFilter(FilterClause, StrictComparativeModel):
    """Reuse the closed filter vocabulary with strict instance revalidation."""

    @field_validator("value", mode="before")
    @classmethod
    def json_array(cls, value, info):
        return tuple(value) if info.mode == "json" and isinstance(value, list) else value


class ComparativeAnalysisPlan(StrictComparativeModel):
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    operation: Literal["compare", "mom", "contribution"]
    current_period: PeriodSpec
    baseline_period: PeriodSpec | None = None
    dimension_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    filters: tuple[ComparativeFilter, ...] = Field(default=(), max_length=6)
    top_n: int | None = Field(default=None, ge=1, le=MAX_TOP_N)
    sort_direction: Literal["asc", "desc"] = "desc"

    @field_validator("filters", mode="before")
    @classmethod
    def json_array(cls, value, info):
        return tuple(value) if info.mode == "json" and isinstance(value, list) else value

    @model_validator(mode="after")
    def periods_and_capabilities(self):
        current, baseline = self.current_period, self.baseline_period
        if self.operation == "mom":
            expected = previous_month(current)
            if baseline is not None and (
                baseline.start_date, baseline.end_date, baseline.granularity
            ) != (expected.start_date, expected.end_date, expected.granularity):
                raise ValueError("mom baseline must be the immediately preceding month")
            object.__setattr__(self, "baseline_period", expected)
            baseline = expected
        if baseline is None:
            raise ValueError("explicit baseline_period is required")
        if current.granularity != baseline.granularity:
            raise ValueError("period granularities must match")
        if baseline.end_date >= current.start_date:
            raise ValueError("baseline must precede current without overlap")

        semantic = load_semantic_model()
        if self.metric_id not in semantic.metric_ids:
            raise ValueError("unapproved metric_id")
        metric = semantic.metric(self.metric_id)
        if self.operation not in metric.supported_operations:
            raise ValueError("unsupported comparative operation")
        if self.operation == "contribution":
            if self.dimension_id not in metric.contribution_dimensions:
                raise ValueError("unapproved contribution dimension")
            if not semantic.is_additive_over(self.metric_id, self.dimension_id):
                raise ValueError("non-additive contribution")
        elif self.dimension_id is not None or self.top_n is not None:
            raise ValueError("dimension_id and top_n are contribution-only")

        seen = set()
        for clause in self.filters:
            if clause.dimension_id not in semantic.dimension_ids:
                raise ValueError("unapproved filter dimension")
            dimension = semantic.dimension(clause.dimension_id)
            if not dimension.supports("filter") or dimension.id in seen:
                raise ValueError("unapproved or duplicate filter")
            if dimension.allowed_values is None or any(
                value not in dimension.allowed_values for value in clause.values()
            ):
                raise ValueError("unapproved filter value")
            seen.add(dimension.id)
        return self


def parse_comparative_plan(payload: object) -> ComparativeAnalysisPlan:
    return ComparativeAnalysisPlan.model_validate(payload)


def validate_as_of(payload: object, reference_date: str) -> ComparativeAnalysisPlan:
    """Reject future/unelapsed periods, including 'this month' before month end.

    reference_date is inclusive. This does not parse natural language.
    """
    plan = parse_comparative_plan(payload)
    reference = iso_date(reference_date)
    if iso_date(plan.current_period.end_date) > reference:
        raise ValueError("current period is not complete as of reference_date")
    return plan
