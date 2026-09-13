"""Compute the core metrics from a read-only connection.

Every number here is produced by SQL that was rendered from the semantic config
(see :mod:`eda.metrics.definitions`), and every explanatory note is read from
that same config rather than retyped in Python. If a 口径 changes, it changes in
one place.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from eda.db import fetch_all, fetch_one
from eda.metrics.definitions import (
    AOV_METRIC_ID,
    GMV_METRIC_ID,
    METRIC_REGISTRY,
    ORDER_COUNT_METRIC_ID,
    SEMANTIC,
    MetricFilters,
    build_breakdown_sql,
    build_core_metrics_sql,
)
from eda.metrics.operations import require_implemented_analysis_operation

#: Appended to a result whenever 客单价 could not be computed. Read from
#: semantic/metrics.yaml -> aov_cents.zero_denominator_policy.note_zh, so the
#: declared policy and the runtime behaviour cannot drift apart.
AOV_UNDEFINED_NOTE: str = METRIC_REGISTRY[AOV_METRIC_ID].zero_denominator_note or ""

#: Metrics returned by a breakdown query, in output order.
BREAKDOWN_METRIC_IDS: tuple[str, ...] = (
    GMV_METRIC_ID,
    ORDER_COUNT_METRIC_ID,
    AOV_METRIC_ID,
)

assert AOV_UNDEFINED_NOTE, "aov_cents must declare a zero denominator note in the config"


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
    #: 有效订单成交额（简化口径），整数分。
    effective_order_gmv_cents: int
    valid_order_count: int
    item_quantity: int
    distinct_customers: int
    #: None when ``valid_order_count == 0``; see :data:`AOV_UNDEFINED_NOTE`.
    aov_cents: float | None
    notes: tuple[str, ...] = ()

    @property
    def revenue_cents(self) -> int:
        """Alias kept from stage 1, when this metric was called ``revenue_cents``.

        The canonical name is :attr:`effective_order_gmv_cents`; this alias only
        exists so the stage 1 regression tests and expectation file keep working
        unchanged.
        """
        return self.effective_order_gmv_cents

    @property
    def gmv_yuan(self) -> Decimal:
        result = _cents_to_yuan(self.effective_order_gmv_cents)
        assert result is not None
        return result

    @property
    def revenue_yuan(self) -> Decimal:
        """Alias for :attr:`gmv_yuan` (see :attr:`revenue_cents`)."""
        return self.gmv_yuan

    @property
    def aov_yuan(self) -> Decimal | None:
        return _cents_to_yuan(self.aov_cents)

    def summary_zh(self) -> str:
        gmv_name = METRIC_REGISTRY[GMV_METRIC_ID].name_zh
        order_name = METRIC_REGISTRY[ORDER_COUNT_METRIC_ID].name_zh
        aov_name = METRIC_REGISTRY[AOV_METRIC_ID].name_zh
        aov = f"{self.aov_yuan} 元" if self.aov_yuan is not None else "无定义"
        return (
            f"{self.filters.describe_zh()}：{gmv_name} {self.gmv_yuan} 元，"
            f"{order_name} {self.valid_order_count} 单，{aov_name} {aov}"
        )


class BreakdownRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension_value: str
    effective_order_gmv_cents: int
    valid_order_count: int
    aov_cents: float | None

    @property
    def revenue_cents(self) -> int:
        """Stage 1 alias for :attr:`effective_order_gmv_cents`."""
        return self.effective_order_gmv_cents


class Breakdown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    filters: MetricFilters
    rows: tuple[BreakdownRow, ...]
    notes: tuple[str, ...] = ()

    @property
    def total_effective_order_gmv_cents(self) -> int:
        return sum(row.effective_order_gmv_cents for row in self.rows)

    @property
    def total_revenue_cents(self) -> int:
        """Stage 1 alias for :attr:`total_effective_order_gmv_cents`."""
        return self.total_effective_order_gmv_cents


def compute_core_metrics(conn: sqlite3.Connection, filters: MetricFilters) -> CoreMetrics:
    """成交额、有效订单数、客单价、销量、客户数 for one filter set."""
    for metric_id in (GMV_METRIC_ID, ORDER_COUNT_METRIC_ID, AOV_METRIC_ID):
        require_implemented_analysis_operation(METRIC_REGISTRY[metric_id].spec, "total")

    sql, params = build_core_metrics_sql(filters)
    row = fetch_one(conn, sql, params)
    if row is None:  # aggregate query without GROUP BY always returns one row
        raise RuntimeError("core metrics query returned no row")

    gmv_cents = int(row[GMV_METRIC_ID])
    valid_order_count = int(row[ORDER_COUNT_METRIC_ID])

    notes: list[str] = []
    if valid_order_count == 0:
        aov_cents: float | None = None
        notes.append(AOV_UNDEFINED_NOTE)
    else:
        # Computed in Python from two exact integers rather than in SQL, so the
        # division cannot silently become integer division.
        aov_cents = gmv_cents / valid_order_count

    return CoreMetrics(
        filters=filters,
        effective_order_gmv_cents=gmv_cents,
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
    for metric_id in BREAKDOWN_METRIC_IDS:
        require_implemented_analysis_operation(METRIC_REGISTRY[metric_id].spec, "breakdown")

    sql, params = build_breakdown_sql(dimension, filters, limit=limit)
    rows = tuple(
        BreakdownRow(
            dimension_value=str(row["dimension_value"]),
            effective_order_gmv_cents=int(row[GMV_METRIC_ID]),
            valid_order_count=int(row[ORDER_COUNT_METRIC_ID]),
            aov_cents=None if row[AOV_METRIC_ID] is None else float(row[AOV_METRIC_ID]),
        )
        for row in fetch_all(conn, sql, params)
    )

    # The warning about non-additive order counts is declared on the dimension
    # in semantic/dimensions.yaml, not written out here.
    spec = SEMANTIC.dimension(dimension)
    notes: list[str] = []
    if spec.non_additive_note_zh and set(spec.non_additive_metrics) & set(
        BREAKDOWN_METRIC_IDS
    ):
        notes.append(spec.non_additive_note_zh)
    if not rows:
        notes.append(
            f"筛选条件 {filters.describe_zh()} 下没有 "
            f"{'/'.join(METRIC_REGISTRY[GMV_METRIC_ID].status_include)} 订单明细，"
            f"按 {spec.name_zh}（{spec.source_field}）的拆分结果为空。"
        )
    return Breakdown(dimension=dimension, filters=filters, rows=rows, notes=tuple(notes))
