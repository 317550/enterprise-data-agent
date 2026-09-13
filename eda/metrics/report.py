"""Read-only command line report -- a way to eyeball the 口径 without any LLM.

    python -m eda.metrics.report --db data\\fixture.db
    python -m eda.metrics.report --start 2024-06-01 --end 2024-06-30 --by region

Everything here is plain SQL against the read-only connection. There is no
model, no network call and no code generation: this is the manual
cross-check tool that stage 3's agent output will later be compared against.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from eda.config import get_settings
from eda.db import DatabaseError, connect_readonly
from eda.metrics.core import compute_breakdown, compute_core_metrics
from eda.metrics.definitions import BREAKDOWN_DIMENSIONS, METRIC_REGISTRY, MetricFilters


def _yuan(cents: int | float | None) -> str:
    if cents is None:
        return "-"
    return f"{Decimal(str(cents)) / 100:,.2f}"


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
        "--show-definitions", action="store_true", help="print the metric 口径 and caveats"
    )
    return parser


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
        print(f"  营业额(简化口径) : {_yuan(metrics.revenue_cents)} 元"
              f"  ({metrics.revenue_cents} 分)")
        print(f"  有效订单量       : {metrics.valid_order_count} 单")
        print(f"  客单价           : {_yuan(metrics.aov_cents)} 元")
        print(f"  有效销量         : {metrics.item_quantity} 件")
        print(f"  下单客户数       : {metrics.distinct_customers} 人")
        for note in metrics.notes:
            print(f"  ! {note}")

        if args.by:
            breakdown = compute_breakdown(conn, args.by, filters, limit=args.limit)
            print(f"\n按 {args.by} 拆分:")
            print(f"  {'取值':<14}{'营业额(元)':>16}{'订单数':>10}{'客单价(元)':>14}")
            for row in breakdown.rows:
                print(
                    f"  {row.dimension_value:<14}{_yuan(row.revenue_cents):>16}"
                    f"{row.valid_order_count:>10}{_yuan(row.aov_cents):>14}"
                )
            for note in breakdown.notes:
                print(f"  ! {note}")

        if args.show_definitions:
            print("\n指标口径:")
            for definition in METRIC_REGISTRY.values():
                print(f"  [{definition.key}] {definition.name_zh} ({definition.unit})")
                print(f"      定义: {definition.definition_zh}")
                print(f"      基表: {definition.base_view}  粒度: {definition.grain}")
                for caveat in definition.caveats:
                    print(f"      注意: {caveat}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
