"""Pure Decimal arithmetic on supplied values; no database/model access.

Values and deltas retain their exact decimal representation. Ratios use 12
decimal places, displays use 2, all ROUND_HALF_UP in an isolated context.
Inputs are bounded to keep sums/subtractions exact under that context.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from typing import Literal, Mapping

from eda.plan.comparative import parse_comparative_plan

ARITHMETIC_CONTEXT = Context(prec=128, rounding=ROUND_HALF_UP)
RATE_QUANTUM = Decimal("0.000000000001")
DISPLAY_QUANTUM = Decimal("0.01")
ZERO = Decimal(0)
MAX_DIMENSIONS = 10000


def _decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("finite Decimal values are required")
    if len(value.as_tuple().digits) > 90 or value.as_tuple().exponent < -30 or value.adjusted() > 60:
        raise ValueError("Decimal value exceeds supported precision/range")
    return value


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == 0 else (numerator / denominator).quantize(RATE_QUANTUM)


def display_decimal(value: Decimal | None, *, percent: bool = False) -> str:
    if value is None:
        return "无定义"
    with localcontext(ARITHMETIC_CONTEXT):
        rendered = (value * (100 if percent else 1)).quantize(DISPLAY_QUANTUM)
        if rendered == 0:
            rendered = abs(rendered)
        return f"{rendered:.2f}" + ("%" if percent else "")


@dataclass(frozen=True)
class ComparisonResult:
    current: Decimal
    baseline: Decimal
    absolute_change: Decimal
    change_rate: Decimal | None

    @property
    def display_zh(self) -> dict[str, str]:
        return {"本期": display_decimal(self.current), "基期": display_decimal(self.baseline),
                "变化量": display_decimal(self.absolute_change),
                "变化率": display_decimal(self.change_rate, percent=True)}


def calculate_comparison(current: Decimal, baseline: Decimal) -> ComparisonResult:
    current, baseline = _decimal(current), _decimal(baseline)
    with localcontext(ARITHMETIC_CONTEXT):
        change = current - baseline
        return ComparisonResult(current, baseline, change, _ratio(change, baseline))


@dataclass(frozen=True)
class ContributionRow:
    dimension_value: str
    current: Decimal
    baseline: Decimal
    dimension_change: Decimal
    contribution_rate: Decimal | None

    @property
    def display_zh(self) -> dict[str, str]:
        return {"维度值": self.dimension_value, "本期": display_decimal(self.current),
                "基期": display_decimal(self.baseline),
                "变化量": display_decimal(self.dimension_change),
                "贡献率": display_decimal(self.contribution_rate, percent=True)}


@dataclass(frozen=True)
class ContributionResult:
    status: Literal["success", "reconciliation_failed"]
    metric_id: str
    dimension_id: str
    total: ComparisonResult
    reconciled_change: Decimal
    rows: tuple[ContributionRow, ...]
    hidden_dimension_count: int
    hidden_net_change: Decimal


def _values(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if not isinstance(values, Mapping) or len(values) > MAX_DIMENSIONS:
        raise ValueError("bounded dimension mapping is required")
    result = {}
    for key, value in values.items():
        if type(key) is not str or not key.strip() or len(key) > 128:
            raise ValueError("dimension_value must be a non-empty bounded string")
        result[key] = _decimal(value)
    return result


def calculate_contribution(
    plan: object, current: Mapping[str, Decimal], baseline: Mapping[str, Decimal], *,
    current_total: Decimal, baseline_total: Decimal,
) -> ContributionResult:
    """Reconcile the entire single-dimension partition before display ranking.

    Failed reconciliation returns all rows without contribution rates or top_n.
    Sorting is by signed dimension_change; ties use dimension_value ascending.
    Totals are independently supplied, never inferred from the displayed rows.
    """
    plan = parse_comparative_plan(plan)
    if plan.operation != "contribution":
        raise ValueError("contribution plan is required")
    current, baseline = _values(current), _values(baseline)
    keys = sorted(current.keys() | baseline.keys())
    if len(keys) > MAX_DIMENSIONS:
        raise ValueError("too many aligned dimensions")
    total = calculate_comparison(current_total, baseline_total)
    with localcontext(ARITHMETIC_CONTEXT):
        changes = [(key, current.get(key, ZERO), baseline.get(key, ZERO),
                    current.get(key, ZERO) - baseline.get(key, ZERO)) for key in keys]
        reconciled = sum((row[3] for row in changes), ZERO)
        # Also reject equal offsets in both periods that cancel in the delta.
        success = (reconciled == total.absolute_change
                   and sum(current.values(), ZERO) == total.current
                   and sum(baseline.values(), ZERO) == total.baseline)
        rows = [ContributionRow(key, cur, base, change,
                               _ratio(change, total.absolute_change) if success else None)
                for key, cur, base, change in changes]
        rows.sort(key=lambda row: row.dimension_change, reverse=plan.sort_direction == "desc")
        cutoff = plan.top_n if success and plan.top_n is not None else len(rows)
        hidden = rows[cutoff:]
        return ContributionResult(
            "success" if success else "reconciliation_failed", plan.metric_id, plan.dimension_id,
            total, reconciled, tuple(rows[:cutoff]), len(hidden),
            sum((row.dimension_change for row in hidden), ZERO),
        )
