"""Read-only command line report -- a way to eyeball the 口径 without any LLM.

    python -m eda.metrics.report --db data\\fixture.db
    python -m eda.metrics.report --start 2024-06-01 --end 2024-06-30 --by region

Everything here is plain SQL against the read-only connection. There is no
model, no network call and no code generation: this is the manual cross-check
tool that stage 3's agent output will later be compared against.

All labels, units and caveats are read from the semantic layer
(``semantic/*.yaml``) rather than typed in here, so the report cannot describe a
metric differently from the way it is computed. There is deliberately no flag
that points the loader at a different config directory -- the semantic layer is
a controlled asset, not user input.
"""

from __future__ import annotations

import argparse
import unicodedata
from decimal import Decimal
from pathlib import Path

from eda.config import get_settings
from eda.db import DatabaseError, connect_readonly
from eda.metrics.core import compute_breakdown, compute_core_metrics
from eda.metrics.definitions import (
    AOV_METRIC_ID,
    BREAKDOWN_DIMENSIONS,
    GMV_METRIC_ID,
    METRIC_REGISTRY,
    ORDER_COUNT_METRIC_ID,
    SEMANTIC,
    MetricFilters,
)

#: Core metrics printed for every run, in output order.
_CORE_DISPLAY_ORDER = (
    GMV_METRIC_ID,
    ORDER_COUNT_METRIC_ID,
    AOV_METRIC_ID,
    "item_quantity",
    "distinct_customers",
)


def _yuan(cents: int | float | None) -> str:
    if cents is None:
        return "-"
    return f"{Decimal(str(cents)) / 100:,.2f}"


def _display_width(text: str) -> int:
    """Terminal columns occupied, counting CJK characters as two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, *, align: str = "left") -> str:
    filler = " " * max(0, width - _display_width(text))
    return text + filler if align == "left" else filler + text


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m eda.metrics.report",
        description="Print the v1 metrics for a date range (read-only).",
    )
    parser.add_argument("--db", type=Path, default=settings.business_db_path)
    parser.add_argument("--start", default=settings.demo_start_date)
    parser.add_argument("--end", default=settings.demo_end_date)
    parser.add_argument("--region", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument(
        "--by",
        choices=sorted(BREAKDOWN_DIMENSIONS),
        default=None,
        help="also print a breakdown by this dimension",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--show-definitions",
        action="store_true",
        help="print the metric 口径 and caveats straight from semantic/metrics.yaml",
    )
    return parser


def _print_core(metrics) -> None:
    """Print the core bundle using labels from the semantic layer."""
    values: dict[str, int | float | None] = {
        GMV_METRIC_ID: metrics.effective_order_gmv_cents,
        ORDER_COUNT_METRIC_ID: metrics.valid_order_count,
        AOV_METRIC_ID: metrics.aov_cents,
        "item_quantity": metrics.item_quantity,
        "distinct_customers": metrics.distinct_customers,
    }
    label_width = max(
        _display_width(METRIC_REGISTRY[key].name_zh) for key in _CORE_DISPLAY_ORDER
    )
    for key in _CORE_DISPLAY_ORDER:
        definition = METRIC_REGISTRY[key]
        value = values[key]
        label = _pad(definition.name_zh, label_width)
        if definition.unit_code == "cent":
            # Integer cents are shown alongside the yuan figure so the exact
            # value can be cross-checked against the hand calculation.
            print(f"  {label} : {_pad(_yuan(value), 14, align='right')} 元  ({value} 分)")
        elif definition.unit_code == "cent_per_order":
            # A ratio is not an integer number of cents, so no raw figure here.
            print(f"  {label} : {_pad(_yuan(value), 14, align='right')} 元")
        else:
            print(f"  {label} : {_pad(str(value), 14, align='right')} {definition.unit}")


def _print_definitions() -> None:
    print(f"\n指标口径（来自 semantic/metrics.yaml，schema_version={SEMANTIC.schema_version}）:")
    for definition in METRIC_REGISTRY.values():
        aliases = "、".join(definition.legacy_ids) or "无"
        print(f"  [{definition.key}] {definition.name_zh}（{definition.unit}）")
        print(f"      定义: {definition.definition_zh}")
        print(f"      基表: {definition.base_view}  粒度: {definition.grain}  可加: {definition.additive}")
        print(f"      过滤: {definition.filter_policy_zh}")
        print(
            f"      纳入状态: {'/'.join(definition.status_include)}"
            f"  排除状态: {'/'.join(definition.status_exclude)}"
        )
        print(f"      同义词: {'、'.join(definition.synonyms)}  历史标识符: {aliases}")
        print(
            f"      已实现操作: {'、'.join(definition.implemented_operations) or '无'}"
            f"  待实现: {'、'.join(definition.pending_operations) or '无'}"
        )
        if definition.zero_denominator_note:
            print(f"      零分母: {definition.zero_denominator_note}")
        for caveat in definition.caveats:
            print(f"      注意: {caveat}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        filters = MetricFilters(
            start_date=args.start,
            end_date=args.end,
            region=args.region,
            category=args.category,
        )
    except ValueError as exc:
        print(f"[report] invalid filters: {exc}")
        return 1

    try:
        conn = connect_readonly(args.db)
    except DatabaseError as exc:
        print(f"[report] {exc}")
        return 1

    try:
        metrics = compute_core_metrics(conn, filters)
        print(f"筛选条件: {filters.describe_zh()}")
        _print_core(metrics)
        for note in metrics.notes:
            print(f"  ! {note}")

        if args.by:
            breakdown = compute_breakdown(conn, args.by, filters, limit=args.limit)
            dimension = SEMANTIC.dimension(args.by)
            gmv = METRIC_REGISTRY[GMV_METRIC_ID]
            orders = METRIC_REGISTRY[ORDER_COUNT_METRIC_ID]
            value_width = max(
                [_display_width(dimension.name_zh)]
                + [_display_width(row.dimension_value) for row in breakdown.rows]
            )
            print(f"\n按{dimension.name_zh}（{dimension.source_field}）拆分:")
            print(
                f"  {_pad(dimension.name_zh, value_width)}"
                f"{_pad(gmv.name_zh + '(元)', 32, align='right')}"
                f"{_pad(orders.name_zh, 12, align='right')}"
                f"{_pad(dimension.aov_display_name_zh + '(元)', 20, align='right')}"
            )
            for row in breakdown.rows:
                print(
                    f"  {_pad(row.dimension_value, value_width)}"
                    f"{_pad(_yuan(row.effective_order_gmv_cents), 32, align='right')}"
                    f"{_pad(str(row.valid_order_count), 12, align='right')}"
                    f"{_pad(_yuan(row.aov_cents), 20, align='right')}"
                )
            for note in breakdown.notes:
                print(f"  ! {note}")

        if args.show_definitions:
            _print_definitions()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
