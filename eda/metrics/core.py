"""Compute the core metrics from a read-only connection."""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from eda.db import fetch_all, fetch_one
from eda.metrics.definitions import (
    BREAKDOWN_DIMENSIONS,
    MetricFilters,
    build_breakdown_sql,
    build_core_metrics_sql,
)

#: Appended to a result whenever AOV could not be computed.
AOV_UNDEFINED_NOTE = (
    "有效订单量为 0，客单价无定义（返回空值而非 0）。"
    "请确认时间范围、地区或类别筛选是否过窄，或该区间确实没有 paid/completed 订单。"
)

_CATEGORY_BREAKDOWN_NOTE = (
    "按商品类别拆分时，一个订单可能横跨多个类别："
    "各类别的营业额相加等于总营业额，但各类别的订单数相加会大于总订单数"
    "（每个类别统计的是“包含该类别商品的订单数”）。"
)


def _cents_to_yuan(cents: int | float | None) -> Decimal | None:
    """Format for display only. All arithmetic happens in integer cents."""
    if cents is None:
        return None
    return (Decimal(str(cents)) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


class CoreMetrics(BaseModel):
    """The v1 metric bundle for one filter combination."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filters: MetricFilters
    revenue_cents: int
    valid_order_count: int
    item_quantity: int
    distinct_customers: int
    #: None when ``valid_order_count == 0``; see :data:`AOV_UNDEFINED_NOTE`.
    aov_cents: float | None
    notes: tuple[str, ...] = ()

    @property
    def revenue_yuan(self) -> Decimal:
        result = _cents_to_yuan(self.revenue_cents)
        assert result is not None
        return result

    @property
    def aov_yuan(self) -> Decimal | None:
        return _cents_to_yuan(self.aov_cents)

    def summary_zh(self) -> str:
        aov = f"{self.aov_yuan} 元" if self.aov_yuan is not None else "无定义"
        return (
            f"{self.filters.describe_zh()}：营业额 {self.revenue_yuan} 元，"
            f"有效订单 {self.valid_order_count} 单，客单价 {aov}"
        )


class BreakdownRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension_value: str
    revenue_cents: int
    valid_order_count: int
    aov_cents: float | None


class Breakdown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    filters: MetricFilters
    rows: tuple[BreakdownRow, ...]
    notes: tuple[str, ...] = ()

    @property
    def total_revenue_cents(self) -> int:
        return sum(row.revenue_cents for row in self.rows)


def compute_core_metrics(conn: sqlite3.Connection, filters: MetricFilters) -> CoreMetrics:
    """Revenue, valid orders, AOV, quantity and customers for one filter set."""
    sql, params = build_core_metrics_sql(filters)
    row = fetch_one(conn, sql, params)
    if row is None:  # aggregate query without GROUP BY always returns one row
        raise RuntimeError("core metrics query returned no row")

    revenue_cents = int(row["revenue_cents"])
    valid_order_count = int(row["valid_order_count"])

    notes: list[str] = []
    if valid_order_count == 0:
        aov_cents: float | None = None
        notes.append(AOV_UNDEFINED_NOTE)
    else:
        # Computed in Python from two exact integers rather than in SQL, so the
        # division cannot silently become integer division.
        aov_cents = revenue_cents / valid_order_count

    return CoreMetrics(
        filters=filters,
        revenue_cents=revenue_cents,
        valid_order_count=valid_order_count,
        item_quantity=int(row["item_quantity"]),
        distinct_customers=int(row["distinct_customers"]),
        aov_cents=aov_cents,
        notes=tuple(notes),
    )


def compute_breakdown(
    conn: sqlite3.Connection,
    dimension: str,
    filters: MetricFilters,
    *,
    limit: int | None = None,
) -> Breakdown:
    """Group the core metrics by one allow-listed dimension."""
    sql, params = build_breakdown_sql(dimension, filters, limit=limit)
    rows = tuple(
        BreakdownRow(
            dimension_value=str(row["dimension_value"]),
            revenue_cents=int(row["revenue_cents"]),
            valid_order_count=int(row["valid_order_count"]),
            aov_cents=None if row["aov_cents"] is None else float(row["aov_cents"]),
        )
        for row in fetch_all(conn, sql, params)
    )

    notes: list[str] = []
    if dimension in {"category", "product"}:
        notes.append(_CATEGORY_BREAKDOWN_NOTE)
    if not rows:
        notes.append(
            f"筛选条件 {filters.describe_zh()} 下没有 paid/completed 订单明细，"
            f"按 {BREAKDOWN_DIMENSIONS[dimension]} 的拆分结果为空。"
        )
    return Breakdown(dimension=dimension, filters=filters, rows=rows, notes=tuple(notes))
