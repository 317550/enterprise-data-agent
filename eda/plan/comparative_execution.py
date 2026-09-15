"""Deterministic expansion of the closed comparative protocol."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from eda.plan.comparative import (
    ComparativeAnalysisPlan, StrictComparativeModel, _export, parse_comparative_plan,
)
from eda.plan.models import AnalysisPlan

StepRole = Literal[
    "baseline_total", "current_total", "baseline_breakdown", "current_breakdown"
]
TOTAL_ROLES = ("baseline_total", "current_total")
CONTRIBUTION_ROLES = TOTAL_ROLES + ("baseline_breakdown", "current_breakdown")


class ComparativeExecutionStep(StrictComparativeModel):
    step_id: StepRole
    evidence_id: StepRole
    role: StepRole
    analysis_plan: AnalysisPlan

    @field_validator("analysis_plan", mode="before")
    @classmethod
    def validate_child(cls, value):
        return AnalysisPlan.model_validate(_export(value), strict=True)

    @model_validator(mode="after")
    def fixed_identity(self):
        if self.step_id != self.role or self.evidence_id != self.role:
            raise ValueError("step identities are fixed by code")
        if self.analysis_plan.operation != self.role.split("_", 1)[1]:
            raise ValueError("step role must match operation")
        if self.analysis_plan.top_n is not None:
            raise ValueError("execution must retain the full population")
        return self


def _steps(plan: ComparativeAnalysisPlan) -> tuple[ComparativeExecutionStep, ...]:
    roles = CONTRIBUTION_ROLES if plan.operation == "contribution" else TOTAL_ROLES
    result = []
    for role in roles:
        period_name, operation = role.split("_", 1)
        period = getattr(plan, period_name + "_period")
        child = AnalysisPlan.model_validate(dict(
            metric_id=plan.metric_id, operation=operation,
            start_date=period.start_date, end_date=period.end_date,
            filters=tuple(clause.model_dump() for clause in plan.filters),
            dimension_id=plan.dimension_id if operation == "breakdown" else None,
        ), strict=True)
        result.append(ComparativeExecutionStep(
            step_id=role, evidence_id=role, role=role, analysis_plan=child,
        ))
    return tuple(result)


class ComparativeExecutionPlan(StrictComparativeModel):
    plan: ComparativeAnalysisPlan
    steps: tuple[ComparativeExecutionStep, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def only_deterministic_steps(self):
        if self.steps != _steps(self.plan):
            raise ValueError("steps must exactly match deterministic expansion")
        return self


def compile_comparative_plan(payload: object) -> ComparativeExecutionPlan:
    plan = parse_comparative_plan(payload)
    return ComparativeExecutionPlan(plan=plan, steps=_steps(plan))
