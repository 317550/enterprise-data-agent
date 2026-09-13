"""Metric definitions: the single source of truth for 口径 (how a number is defined).

This module produces SQL *text* and bound parameters. It never executes
anything -- execution lives behind :mod:`eda.db` today and behind the safe
executor from stage 2 onwards. Keeping definition and execution apart is what
lets later stages reuse the exact same SQL the tests verify.

The three v1 metrics
--------------------
revenue_cents      SUM(quantity * unit_price_cents) over order lines whose order
                   status is 'paid' or 'completed'. Simplified gross figure:
                   refunds are NOT netted out, so this is 营业额, not 净收入.
valid_order_count  COUNT(DISTINCT order_id) over the same base. Distinct, so a
                   3-line order counts once.
aov_cents          revenue_cents / valid_order_count; undefined (None) when the
                   denominator is 0.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from eda.domain.enums import (
    EXCLUDED_FROM_REVENUE_STATUSES,
    REVENUE_STATUSES,
    Category,
    Region,
)

#: Canonical line-grain base view for every revenue number.
REVENUE_BASE_VIEW: Final[str] = "v_revenue_lines"
#: Canonical order-grain base view, for order-level distributions.
REVENUE_ORDER_VIEW: Final[str] = "v_revenue_orders"


class MetricDefinition(BaseModel):
    """Machine- and human-readable description of one metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name_zh: str
    unit: str
    grain: str
    base_view: str
    sql_expression: str
    definition_zh: str
    caveats: tuple[str, ...] = ()


_REVENUE_STATUS_TEXT = " / ".join(REVENUE_STATUSES)
_EXCLUDED_STATUS_TEXT = " / ".join(EXCLUDED_FROM_REVENUE_STATUSES)

METRIC_REGISTRY: Final[dict[str, MetricDefinition]] = {
    definition.key: definition
    for definition in (
        MetricDefinition(
            key="revenue_cents",
            name_zh="营业额（简化口径）",
            unit="分",
            grain="order line",
            base_view=REVENUE_BASE_VIEW,
            sql_expression="COALESCE(SUM(line_amount_cents), 0)",
            definition_zh=(
                f"状态为 {_REVENUE_STATUS_TEXT} 的订单，其订单明细金额"
                "（数量 × 成交单价）之和。"
            ),
            caveats=(
                f"{_EXCLUDED_STATUS_TEXT} 状态的订单整单排除。",
                "不扣减退款金额，因此这是简化的营业额（gross），不是净收入（net revenue）。",
                "使用成交单价 unit_price_cents，不使用商品目录价 list_price_cents。",
            ),
        ),
        MetricDefinition(
            key="valid_order_count",
            name_zh="有效订单量",
            unit="单",
            grain="order",
            base_view=REVENUE_BASE_VIEW,
            sql_expression="COUNT(DISTINCT order_id)",
            definition_zh=(
                f"状态为 {_REVENUE_STATUS_TEXT} 且至少包含一条明细的订单数，按 order_id 去重。"
            ),
            caveats=(
                "必须 COUNT(DISTINCT order_id)；在明细粒度上 COUNT(*) 会把多行订单重复计数。",
                "没有任何明细行的订单不计入（本项目的数据生成保证每单至少一行）。",
            ),
        ),
        MetricDefinition(
            key="aov_cents",
            name_zh="客单价",
            unit="分/单",
            grain="derived",
            base_view=REVENUE_BASE_VIEW,
            sql_expression=(
                "CASE WHEN COUNT(DISTINCT order_id) = 0 THEN NULL "
                "ELSE 1.0 * COALESCE(SUM(line_amount_cents), 0) / COUNT(DISTINCT order_id) END"
            ),
            definition_zh="营业额 ÷ 有效订单量。",
            caveats=(
                "分母为 0 时返回空值（NULL/None）并附带说明，不返回 0。",
                "计算过程不做四舍五入；只有展示时才格式化为两位小数的元。",
            ),
        ),
        MetricDefinition(
            key="item_quantity",
            name_zh="有效销量",
            unit="件",
            grain="order line",
            base_view=REVENUE_BASE_VIEW,
            sql_expression="COALESCE(SUM(quantity), 0)",
            definition_zh=f"状态为 {_REVENUE_STATUS_TEXT} 的订单明细数量之和。",
        ),
        MetricDefinition(
            key="distinct_customers",
            name_zh="下单客户数",
            unit="人",
            grain="customer",
            base_view=REVENUE_BASE_VIEW,
            sql_expression="COUNT(DISTINCT customer_id)",
            definition_zh=f"状态为 {_REVENUE_STATUS_TEXT} 的订单所涉及的去重客户数。",
        ),
    )
}

#: Dimension name -> column in ``v_revenue_lines``. A strict allow-list: the
#: dimension never reaches the SQL string unless it is a key of this mapping.
BREAKDOWN_DIMENSIONS: Final[dict[str, str]] = {
    "month": "order_month",
    "date": "order_date",
    "region": "order_region",
    "category": "category",
    "channel": "order_channel",
    "product": "product_name",
}

_CORE_METRIC_KEYS: Final[tuple[str, ...]] = (
    "revenue_cents",
    "valid_order_count",
    "item_quantity",
    "distinct_customers",
)

#: Shared WHERE clause. ``:region`` / ``:category`` are optional filters: when
#: bound to NULL the condition is a no-op, so one SQL string covers all cases.
_WHERE = (
    "WHERE order_date >= :start_date\n"
    "  AND order_date <= :end_date\n"
    "  AND (:region IS NULL OR order_region = :region)\n"
    "  AND (:category IS NULL OR category = :category)"
)


class MetricFilters(BaseModel):
    """Time range plus optional slicing. ``end_date`` is inclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: str
    end_date: str
    region: str | None = None
    category: str | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        import datetime as dt

        dt.date.fromisoformat(value)
        return value

    @field_validator("region")
    @classmethod
    def _known_region(cls, value: str | None) -> str | None:
        return None if value is None else Region(value).value

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        return None if value is None else Category(value).value

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
        parts = [f"{self.start_date} 至 {self.end_date}"]
        if self.region:
            parts.append(f"地区={self.region}")
        if self.category:
            parts.append(f"类别={self.category}")
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
    """SQL for revenue / orders / AOV grouped by one allow-listed dimension."""
    try:
        column = BREAKDOWN_DIMENSIONS[dimension]
    except KeyError:
        raise ValueError(
            f"unknown breakdown dimension {dimension!r}; "
            f"allowed: {sorted(BREAKDOWN_DIMENSIONS)}"
        ) from None

    sql = (
        f"SELECT\n"
        f"    {column} AS dimension_value,\n"
        f"    {METRIC_REGISTRY['revenue_cents'].sql_expression} AS revenue_cents,\n"
        f"    {METRIC_REGISTRY['valid_order_count'].sql_expression} AS valid_order_count,\n"
        f"    {METRIC_REGISTRY['aov_cents'].sql_expression} AS aov_cents\n"
        f"FROM {REVENUE_BASE_VIEW}\n"
        f"{_WHERE}\n"
        f"GROUP BY {column}\n"
        f"ORDER BY revenue_cents DESC, dimension_value ASC"
    )
    params = filters.as_params()
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql += "\nLIMIT :row_limit"
        params["row_limit"] = int(limit)
    return sql, params
