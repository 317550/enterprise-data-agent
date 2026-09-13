"""Pydantic AnalysisPlan: is this business request legal?

This module does not compile SQL and does not talk to SQLite. It only decides
whether a request names approved semantic objects and a supported field combo.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eda.metrics.definitions import METRIC_REGISTRY, SEMANTIC, resolve_metric
from eda.metrics.operations import (
    IMPLEMENTED_ANALYSIS_OPERATIONS,
    UnsupportedOperationError,
    require_implemented_analysis_operation,
)
from eda.query.errors import QueryError

#: Conservative cap for IN lists. Not a SQL expression language.
MAX_FILTER_VALUES = 20
#: Conservative cap for breakdown ranking. Ranking is breakdown + order + top_n.
MAX_TOP_N = 100
MAX_FILTER_VALUE_LENGTH = 128
FilterValue = Annotated[str, Field(min_length=1, max_length=MAX_FILTER_VALUE_LENGTH)]

_IDENTIFIER = r"^[a-z][a-z0-9_]*$"
PlanOperation = Literal["total", "breakdown"]
FilterOp = Literal["eq", "in"]
OrderBy = Literal["metric_value", "dimension_value"]
SortDirection = Literal["asc", "desc"]


class FilterClause(BaseModel):
    """One approved-dimension predicate: equality or a short IN list."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    dimension_id: str = Field(pattern=_IDENTIFIER)
    op: FilterOp
    value: FilterValue | tuple[FilterValue, ...]

    @model_validator(mode="after")
    def _value_matches_op(self) -> FilterClause:
        if self.op == "eq":
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("eq filter requires a non-empty string value")
        else:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError("in filter requires a non-empty list of strings")
            if len(self.value) > MAX_FILTER_VALUES:
                raise ValueError(
                    f"in filter accepts at most {MAX_FILTER_VALUES} values"
                )
            if any(not isinstance(item, str) or not item for item in self.value):
                raise ValueError("in filter values must be non-empty strings")
            if len(set(self.value)) != len(self.value):
                raise ValueError("in filter values must be unique")
        return self

    def values(self) -> tuple[str, ...]:
        return (self.value,) if isinstance(self.value, str) else self.value


class AnalysisPlan(BaseModel):
    """A single-metric total or breakdown request.

    Extra fields are forbidden so a caller cannot smuggle SQL, table names,
    file paths or expressions into the plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    metric_id: str = Field(pattern=_IDENTIFIER)
    operation: PlanOperation
    start_date: str
    end_date: str
    dimension_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    filters: tuple[FilterClause, ...] = ()
    order_by: OrderBy | None = None
    sort_direction: SortDirection | None = None
    top_n: int | None = Field(default=None, ge=1, le=MAX_TOP_N, strict=True)

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        if dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError("dates must use YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def _dates_are_closed_and_ordered(self) -> AnalysisPlan:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self

    @model_validator(mode="after")
    def _operation_fields_match(self) -> AnalysisPlan:
        if self.operation == "total":
            if self.dimension_id is not None:
                raise ValueError("total does not accept dimension_id")
            if self.order_by is not None or self.sort_direction is not None:
                raise ValueError("total does not accept order_by or sort_direction")
            if self.top_n is not None:
                raise ValueError("top_n is only valid for breakdown")
            return self

        if self.dimension_id is None:
            raise ValueError("breakdown requires dimension_id")
        # Ranking is breakdown + order + optional top_n; defaults are explicit.
        if self.order_by is None:
            object.__setattr__(self, "order_by", "metric_value")
        if self.sort_direction is None:
            object.__setattr__(self, "sort_direction", "desc")
        return self

    @model_validator(mode="after")
    def _semantic_objects_are_approved(self) -> AnalysisPlan:
        try:
            definition = resolve_metric(self.metric_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        if definition.key != self.metric_id and self.metric_id not in definition.legacy_ids:
            raise ValueError(f"unknown metric_id {self.metric_id!r}")
        # Plans use the canonical id; legacy ids are accepted then rewritten.
        if self.metric_id != definition.key:
            object.__setattr__(self, "metric_id", definition.key)

        if self.operation not in IMPLEMENTED_ANALYSIS_OPERATIONS:
            raise ValueError(
                f"operation {self.operation!r} is not implemented; "
                f"implemented: {sorted(IMPLEMENTED_ANALYSIS_OPERATIONS)}"
            )
        try:
            require_implemented_analysis_operation(definition.spec, self.operation)
        except UnsupportedOperationError as exc:
            raise ValueError(str(exc)) from exc

        if self.dimension_id is not None:
            try:
                dimension = SEMANTIC.dimension(self.dimension_id)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            if self.dimension_id != dimension.id:
                object.__setattr__(self, "dimension_id", dimension.id)
            if "group_by" not in dimension.allowed_operations:
                raise ValueError(
                    f"dimension {dimension.id!r} is not approved for breakdown"
                )
            if dimension.id not in definition.allowed_dimensions:
                raise ValueError(
                    f"metric {definition.key!r} does not allow dimension {dimension.id!r}"
                )

        seen_filter_dims: set[str] = set()
        rewritten: list[FilterClause] = []
        for clause in self.filters:
            try:
                dimension = SEMANTIC.dimension(clause.dimension_id)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            if "filter" not in dimension.allowed_operations:
                raise ValueError(
                    f"dimension {dimension.id!r} is not approved for filtering"
                )
            if dimension.id in seen_filter_dims:
                raise ValueError(f"duplicate filter on dimension {dimension.id!r}")
            seen_filter_dims.add(dimension.id)
            allowed = dimension.allowed_values
            if allowed is not None:
                unknown = [value for value in clause.values() if value not in allowed]
                if unknown:
                    raise ValueError(
                        f"unknown {dimension.id} value {unknown[0]!r}; "
                        f"allowed: {list(allowed)}"
                    )
            if clause.dimension_id != dimension.id:
                clause = clause.model_copy(update={"dimension_id": dimension.id})
            rewritten.append(clause)
        if rewritten != list(self.filters):
            object.__setattr__(self, "filters", tuple(rewritten))
        return self


def parse_analysis_plan(payload: dict[str, Any] | AnalysisPlan) -> AnalysisPlan:
    """Parse a mapping into a plan, mapping validation failures to invalid_plan."""
    try:
        if isinstance(payload, AnalysisPlan):
            payload = payload.model_dump()
        return AnalysisPlan.model_validate(payload)
    except Exception as exc:
        raise QueryError("invalid_plan", "analysis plan is invalid") from exc


# Touch the registry so a broken semantic layer fails at import, not at the CLI.
_ = METRIC_REGISTRY
