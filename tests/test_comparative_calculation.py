from decimal import Decimal as D, localcontext, ROUND_DOWN

import pytest
from pydantic import ValidationError

from eda.metrics.comparative import calculate_comparison, calculate_contribution, display_decimal
from eda.plan.comparative import ComparativeAnalysisPlan, month_period


def plan(**updates):
    return ComparativeAnalysisPlan(**(dict(metric_id="effective_order_gmv_cents", operation="contribution",
        dimension_id="region", current_period=month_period(2024, 2),
        baseline_period=month_period(2024, 1)) | updates))


@pytest.mark.parametrize("current,baseline,change,rate,display", [
    ("120", "100", "20", "0.2", "20.00%"),
    ("80", "100", "-20", "-0.2", "-20.00%"),
    ("0", "100", "-100", "-1", "-100.00%"),
    ("100", "0", "100", None, "无定义"),
    ("0", "0", "0", None, "无定义"),
    ("100", "100", "0", "0", "0.00%"),
    ("-80", "-100", "20", "-0.2", "-20.00%"),
    ("4", "3", "1", "0.333333333333", "33.33%"),
    ("2", "3", "-1", "-0.333333333333", "-33.33%"),
])
def test_comparison(current, baseline, change, rate, display):
    result = calculate_comparison(D(current), D(baseline))
    assert result.absolute_change == D(change)
    assert result.change_rate == (None if rate is None else D(rate))
    assert result.display_zh["变化率"] == display


def test_rounding_and_context_independence():
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_DOWN
        result = calculate_comparison(D("123456.005"), D("0.001"))
        assert result.absolute_change == D("123456.004")
        assert result.display_zh["本期"] == "123456.01"
        assert display_decimal(D("-1.005")) == "-1.01"
        assert calculate_comparison(D("1.0000000000005"), D(1)).change_rate == D("0.000000000001")
        assert calculate_comparison(D("0.9999999999995"), D(1)).change_rate == D("-0.000000000001")
    assert result.current == D("123456.005")


@pytest.mark.parametrize("value", [1.1, True, "1", D("NaN"), D("Infinity"), D("1e100"), D("1e-31")])
def test_reject_inexact_or_unbounded_inputs(value):
    with pytest.raises(ValueError):
        calculate_comparison(value, D(1))


def test_align_missing_dimensions_and_cancellation():
    result = calculate_contribution(plan(), {"A": D(30), "C": D(5)}, {"B": D(20)},
                                    current_total=D(35), baseline_total=D(20))
    assert result.status == "success"
    rows = {r.dimension_value: r for r in result.rows}
    assert rows["A"].baseline == 0 and rows["B"].current == 0
    assert rows["A"].contribution_rate == D(2)
    assert rows["B"].contribution_rate == D("-1.333333333333")
    assert rows["A"].display_zh["贡献率"] == "200.00%"
    assert sum(r.dimension_change for r in result.rows) == result.total.absolute_change == D(15)


def test_zero_total_change_with_nonzero_components():
    result = calculate_contribution(plan(), {"A": D(10)}, {"B": D(10)},
                                    current_total=D(10), baseline_total=D(10))
    assert result.status == "success" and result.reconciled_change == 0
    assert all(r.contribution_rate is None for r in result.rows)


def test_empty_periods():
    result = calculate_contribution(plan(), {}, {}, current_total=D(0), baseline_total=D(0))
    assert result.status == "success" and result.rows == ()
    assert result.total.change_rate is None and result.hidden_net_change == 0


@pytest.mark.parametrize("top_n", [None, 1])
def test_reconciliation_failure_precedes_top_n(top_n):
    result = calculate_contribution(plan(top_n=top_n), {"A": D(30), "B": D(5)}, {},
                                    current_total=D(30), baseline_total=D(0))
    assert result.status == "reconciliation_failed"
    assert result.reconciled_change == D(35)
    assert len(result.rows) == 2 and all(r.contribution_rate is None for r in result.rows)
    assert result.hidden_dimension_count == 0


def test_reject_equal_offsets_in_period_totals():
    result = calculate_contribution(plan(), {"A": D(15)}, {"A": D(5)},
                                    current_total=D(20), baseline_total=D(10))
    assert result.reconciled_change == result.total.absolute_change
    assert result.status == "reconciliation_failed"


@pytest.mark.parametrize("direction,shown,hidden", [("desc", "A", "-15"), ("asc", "B", "35")])
def test_top_n_only_changes_display(direction, shown, hidden):
    args = dict(current={"A": D(30), "C": D(5)}, baseline={"B": D(20)},
                current_total=D(35), baseline_total=D(20))
    full = calculate_contribution(plan(), **args)
    limited = calculate_contribution(plan(top_n=1, sort_direction=direction), **args)
    assert limited.status == full.status == "success"
    assert limited.total == full.total and limited.reconciled_change == full.reconciled_change
    assert limited.rows[0].dimension_value == shown
    assert limited.hidden_dimension_count == 2 and limited.hidden_net_change == D(hidden)
    assert limited.rows[0].dimension_change + limited.hidden_net_change == D(15)


def test_ties_are_stable_and_input_order_independent():
    result = calculate_contribution(plan(top_n=1), {"B": D(1), "A": D(1)}, {},
                                    current_total=D(2), baseline_total=D(0))
    assert result.rows[0].dimension_value == "A"


def test_contribution_revalidates_constructed_plan():
    forged = plan().model_copy(update={"metric_id": "aov_cents"})
    with pytest.raises(ValidationError):
        calculate_contribution(forged, {}, {}, current_total=D(0), baseline_total=D(0))


def test_decimal_reconciliation_before_rounding():
    result = calculate_contribution(plan(), {"A": D("0.005"), "B": D("0.005")}, {},
                                    current_total=D("0.010"), baseline_total=D(0))
    assert result.status == "success" and result.reconciled_change == D("0.010")
    assert all(r.dimension_change == D("0.005") for r in result.rows)
