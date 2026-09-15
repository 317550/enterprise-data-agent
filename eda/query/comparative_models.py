"""Immutable comparative output. Every Decimal is a JSON string (Pydantic)."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from eda.metrics.comparative import display_decimal

from eda.plan.comparative import PeriodSpec, StrictComparativeModel
from eda.plan.comparative_execution import ComparativeExecutionStep

ErrorCode = Literal[
    "invalid_plan", "invalid_limits", "budget_exhausted", "timeout",
    "execution_error", "incomplete_result", "missing_query_id",
    "metadata_mismatch", "invalid_result_shape", "undefined_metric",
    "reconciliation_failed",
]


class ComparativeCompleteness(StrictComparativeModel):
    is_complete_population: bool
    display_is_complete_population: bool
    calendar_period_complete: bool
    data_coverage_verified: Literal[False] = False


class ComparativeEvidence(ComparativeExecutionStep):
    query_id: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    completeness: Literal[True] = True
    truncated: Literal[False] = False


class Comparison(StrictComparativeModel):
    current: Decimal = Field(allow_inf_nan=False)
    baseline: Decimal = Field(allow_inf_nan=False)
    absolute_change: Decimal = Field(allow_inf_nan=False)
    change_rate: Decimal | None = Field(allow_inf_nan=False)
    baseline_value: Decimal = Decimal(0)
    current_value: Decimal = Decimal(0)
    change_rate_display: str = ""
    baseline_period: PeriodSpec | None = None
    current_period: PeriodSpec | None = None
    evidence_ids: tuple[Literal["baseline_total"], Literal["current_total"]] = (
        "baseline_total", "current_total",
    )

    @model_validator(mode="after")
    def canonical_output(self):
        # Derived compatibility fields cannot disagree with the exact values.
        object.__setattr__(self, "baseline_value", self.baseline)
        object.__setattr__(self, "current_value", self.current)
        object.__setattr__(self, "change_rate_display", display_decimal(self.change_rate, percent=True))
        return self


class ContributionDetail(StrictComparativeModel):
    dimension_value: str
    current: Decimal
    baseline: Decimal
    dimension_change: Decimal
    contribution_rate: Decimal | None
    baseline_value: Decimal = Decimal(0)
    current_value: Decimal = Decimal(0)
    absolute_change: Decimal = Decimal(0)
    contribution_rate_display: str = ""
    evidence_ids: tuple[Literal["baseline_breakdown"], Literal["current_breakdown"]] = (
        "baseline_breakdown", "current_breakdown",
    )

    @model_validator(mode="after")
    def canonical_output(self):
        object.__setattr__(self, "baseline_value", self.baseline)
        object.__setattr__(self, "current_value", self.current)
        object.__setattr__(self, "absolute_change", self.dimension_change)
        object.__setattr__(self, "contribution_rate_display", display_decimal(self.contribution_rate, percent=True))
        return self


class Contribution(StrictComparativeModel):
    dimension_id: str
    reconciled_change: Decimal
    rows: tuple[ContributionDetail, ...]
    hidden_dimension_count: int = Field(ge=0)
    hidden_net_change: Decimal


class ComparativeAnalysisResult(StrictComparativeModel):
    analysis_id: str
    semantic_version: str
    metric_id: str | None
    metric_name_zh: str | None
    unit: str | None
    unit_code: str | None
    operation: Literal["compare", "mom", "contribution"] | None
    baseline_period: PeriodSpec | None
    current_period: PeriodSpec | None
    query_count: int = Field(ge=0, le=4)
    evidence: tuple[ComparativeEvidence, ...] = Field(max_length=4)
    comparison: Comparison | None
    contribution: Contribution | None
    completeness: ComparativeCompleteness
    warnings: tuple[str, ...]
    error_code: ErrorCode | None
