"""Compiled metric definitions: semantic config in, SQL text out.

Stage 1 kept the 口径 in hand-written Python literals here. Stage 1.1 moved the
business definitions into ``semantic/*.yaml`` so there is exactly one
authoritative statement of what a metric means. This module is now the *compiled
view* of that config:

    semantic/metrics.yaml        business definition (authoritative)
        -> eda.semantic.loader   parse + Pydantic validation + cross-checks
        -> eda.metrics.operations closed set of computation operations
        -> METRIC_REGISTRY       MetricDefinition (spec + rendered SQL)

The module still produces SQL *text* and bound parameters only. It never
executes anything: execution lives behind :mod:`eda.db` today and behind the
safe executor from stage 2 onwards.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from eda.metrics.operations import IMPLEMENTED_ANALYSIS_OPERATIONS, render_metric_sql
from eda.semantic import MetricSpec, SemanticModel, load_semantic_model

#: The loaded semantic model. Imported at module load so a broken config fails
#: immediately and loudly rather than at the first query.
SEMANTIC: Final[SemanticModel] = load_semantic_model()

#: Canonical metric id for 有效订单成交额（简化口径）.
GMV_METRIC_ID: Final[str] = "effective_order_gmv_cents"
ORDER_COUNT_METRIC_ID: Final[str] = "valid_order_count"
AOV_METRIC_ID: Final[str] = "aov_cents"

#: Canonical line-grain base view for every 成交额 number.
REVENUE_BASE_VIEW: Final[str] = SEMANTIC.metric(GMV_METRIC_ID).base_view
#: Canonical order-grain base view, for order-level distributions.
REVENUE_ORDER_VIEW: Final[str] = "v_revenue_orders"


class MetricDefinition(BaseModel):
    """One metric, as the query layer sees it: the spec plus its rendered SQL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name_zh: str
    #: Display unit, e.g. "分" / "单". ``unit_code`` is the machine-readable one.
    unit: str
    unit_code: str
    grain: str
    base_view: str
    sql_expression: str
    definition_zh: str
    caveats: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    legacy_ids: tuple[str, ...] = ()
    allowed_dimensions: tuple[str, ...] = ()
    supported_operations: tuple[str, ...] = ()
    implemented_operations: tuple[str, ...] = ()
    additive: bool = False
    status_include: tuple[str, ...] = ()
    status_exclude: tuple[str, ...] = ()
    filter_policy_zh: str = ""
    zero_denominator_note: str | None = None
    spec: MetricSpec

    @property
    def pending_operations(self) -> tuple[str, ...]:
        """Operations the metric supports in principle but that are 待实现."""
        return tuple(
            operation
            for operation in self.supported_operations
            if operation not in self.implemented_operations
        )


def _compile(spec: MetricSpec) -> MetricDefinition:
    policy = spec.zero_denominator_policy
    return MetricDefinition(
        key=spec.id,
        name_zh=spec.name_zh,
        unit=spec.unit_zh,
        unit_code=spec.unit,
        grain=spec.grain,
        base_view=spec.base_view,
        sql_expression=render_metric_sql(spec, SEMANTIC.metric),
        definition_zh=spec.definition_zh,
        caveats=spec.caveats_zh,
        synonyms=spec.synonyms,
        legacy_ids=spec.legacy_ids,
        allowed_dimensions=spec.allowed_dimensions,
        supported_operations=spec.supported_operations,
        implemented_operations=tuple(
            operation
            for operation in spec.supported_operations
            if operation in IMPLEMENTED_ANALYSIS_OPERATIONS
        ),
        additive=spec.additive,
        status_include=spec.status_include,
        status_exclude=spec.status_exclude,
        filter_policy_zh=spec.filter_policy_zh,
        zero_denominator_note=(
            policy.note_zh if policy.strategy == "return_null_with_note" else None
        ),
        spec=spec,
    )


#: Keyed by canonical metric id only, so ``definition.key == key`` always holds.
#: Use :func:`resolve_metric` to look something up by legacy id or synonym.
METRIC_REGISTRY: Final[dict[str, MetricDefinition]] = {
    spec.id: _compile(spec) for spec in SEMANTIC.metrics
}


def resolve_metric(name: str) -> MetricDefinition:
    """Look a metric up by canonical id, legacy id or Chinese synonym."""
    return METRIC_REGISTRY[SEMANTIC.metric(name).id]


#: Dimension id -> column in the base view. Derived from the semantic config:
#: exactly those dimensions that declare the ``group_by`` operation. A strict
#: allow-list -- a dimension never reaches the SQL string unless it is a key here.
BREAKDOWN_DIMENSIONS: Final[dict[str, str]] = {
    dimension.id: dimension.source_field
    for dimension in SEMANTIC.dimensions_supporting("group_by")
}

#: Dimension ids that may be used as a filter. Must match the fields of
#: :class:`MetricFilters`; ``tests/test_semantic_consistency.py`` asserts it.
FILTER_DIMENSIONS: Final[tuple[str, ...]] = tuple(
    dimension.id for dimension in SEMANTIC.dimensions_supporting("filter")
)

_CORE_METRIC_KEYS: Final[tuple[str, ...]] = (
    GMV_METRIC_ID,
    ORDER_COUNT_METRIC_ID,
    "item_quantity",
    "distinct_customers",
)

#: Shared WHERE clause. ``:region`` / ``:category`` are optional filters: when
#: bound to NULL the condition is a no-op, so one SQL string covers all cases.
#: The date range is a closed interval -- both endpoints are included.
_WHERE = (
    "WHERE order_date >= :start_date\n"
    "  AND order_date <= :end_date\n"
    "  AND (:region IS NULL OR order_region = :region)\n"
    "  AND (:category IS NULL OR category = :category)"
)


def _validate_dimension_value(dimension_id: str, value: str) -> str:
    """Check a filter value against the allowed values in the semantic config."""
    dimension = SEMANTIC.dimension(dimension_id)
    allowed = dimension.allowed_values
    if allowed is not None and value not in allowed:
        raise ValueError(
            f"unknown {dimension_id} value {value!r}; allowed: {list(allowed)}"
        )
    return value


class MetricFilters(BaseModel):
    """Time range plus optional slicing.

    The date range is a **closed interval**: an order dated exactly
    ``start_date`` or exactly ``end_date`` is included.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: str
    end_date: str
    region: str | None = None
    category: str | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        dt.date.fromisoformat(value)
        return value

    @field_validator("region")
    @classmethod
    def _known_region(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dimension_value("region", value)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dimension_value("category", value)

    @model_validator(mode="after")
    def _ordered(self) -> MetricFilters:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self

    def as_params(self) -> dict[str, object]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "region": self.region,
            "category": self.category,
        }

    def describe_zh(self) -> str:
        parts = [f"{self.start_date} 至 {self.end_date}（含端点）"]
        for dimension_id, value in (("region", self.region), ("category", self.category)):
            if value:
                parts.append(f"{SEMANTIC.dimension(dimension_id).name_zh}={value}")
        return "，".join(parts)


def build_core_metrics_sql(filters: MetricFilters) -> tuple[str, dict[str, object]]:
    """SQL for the core metric bundle in a single scan of the revenue base."""
    columns = ",\n    ".join(
        f"{METRIC_REGISTRY[key].sql_expression} AS {key}" for key in _CORE_METRIC_KEYS
    )
    sql = f"SELECT\n    {columns}\nFROM {REVENUE_BASE_VIEW}\n{_WHERE}"
    return sql, filters.as_params()


def build_breakdown_sql(
    dimension: str, filters: MetricFilters, *, limit: int | None = None
) -> tuple[str, dict[str, object]]:
    """SQL for 成交额 / 有效订单数 / 客单价 grouped by one allow-listed dimension."""
    try:
        column = BREAKDOWN_DIMENSIONS[dimension]
    except KeyError:
        raise ValueError(
            f"unknown breakdown dimension {dimension!r}; "
            f"allowed: {sorted(BREAKDOWN_DIMENSIONS)}"
        ) from None

    gmv = METRIC_REGISTRY[GMV_METRIC_ID]
    orders = METRIC_REGISTRY[ORDER_COUNT_METRIC_ID]
    aov = METRIC_REGISTRY[AOV_METRIC_ID]
    sql = (
        f"SELECT\n"
        f"    {column} AS dimension_value,\n"
        f"    {gmv.sql_expression} AS {gmv.key},\n"
        f"    {orders.sql_expression} AS {orders.key},\n"
        f"    {aov.sql_expression} AS {aov.key}\n"
        f"FROM {REVENUE_BASE_VIEW}\n"
        f"{_WHERE}\n"
        f"GROUP BY {column}\n"
        f"ORDER BY {gmv.key} DESC, dimension_value ASC"
    )
    params = filters.as_params()
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql += "\nLIMIT :row_limit"
        params["row_limit"] = int(limit)
    return sql, params
