"""Offline execution, corruption, budget, and independent reference checks."""

from decimal import Decimal
import json
import socket

import pytest
from pydantic import ValidationError

from eda.plan.comparative import ComparativeAnalysisPlan, month_period
from eda.plan.comparative_execution import (
    ComparativeExecutionPlan, compile_comparative_plan,
)
from eda.query.comparative import ComparativeExecutionLimits, run_comparative_analysis
from eda.query.models import ResultRow
from eda.query.service import run_analysis_plan
from eda.query.errors import QueryError


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("comparative execution tests must stay offline")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def plan(operation="compare", **changes):
    payload = dict(metric_id="effective_order_gmv_cents", operation=operation,
                   baseline_period=month_period(2024, 1), current_period=month_period(2024, 2))
    if operation == "contribution":
        payload["dimension_id"] = "region"
    payload.update(changes)
    return ComparativeAnalysisPlan(**payload)


@pytest.mark.parametrize("operation,count", [("compare", 2), ("mom", 2), ("contribution", 4)])
def test_steps(operation, count):
    compiled = compile_comparative_plan(plan(operation))
    roles = ("baseline_total", "current_total", "baseline_breakdown", "current_breakdown")[:count]
    assert tuple(s.role for s in compiled.steps) == roles
    assert all(s.role == s.step_id == s.evidence_id for s in compiled.steps)
    assert all(s.analysis_plan.top_n is None for s in compiled.steps)
    with pytest.raises(ValidationError):
        compiled.steps[0].role = "current_total"


def test_top_n_and_filters():
    from eda.plan.comparative import ComparativeFilter
    source = plan("contribution", top_n=1, filters=(
        ComparativeFilter(dimension_id="region", op="eq", value="华东"),))
    compiled = compile_comparative_plan(source)
    assert all(s.analysis_plan.top_n is None for s in compiled.steps)
    assert all(s.analysis_plan.filters[0].value == "华东" for s in compiled.steps)


@pytest.mark.parametrize("change", [
    {"metric_id": "bogus"}, {"operation": "sql"}, {"dimension_id": ("region", "category")},
    {"metric_id": "aov_cents"}, {"metric_id": "distinct_customers"},
    {"metric_id": "valid_order_count", "dimension_id": "category"},
    {"top_n": True}, {"sql": "SELECT 1"}, {"evidence_id": "custom"},
])
def test_construct_cannot_bypass(change):
    forged = plan("contribution").model_copy(update=change)
    with pytest.raises((ValidationError, ValueError)):
        compile_comparative_plan(forged)
    called = []
    result = run_comparative_analysis(forged, "unused", runner=lambda *a, **k: called.append(1))
    assert result.error_code == "invalid_plan"
    assert result.query_count == 0 and not called


@pytest.mark.parametrize("change", ["reverse", "extra", "child", "identity", "nested"])
def test_execution_construct_revalidated(change):
    compiled = compile_comparative_plan(plan("contribution"))
    steps = compiled.steps
    if change == "reverse":
        steps = steps[::-1]
    elif change == "extra":
        steps = steps + (steps[0],)
    elif change == "identity":
        steps = (steps[0].model_copy(update={"evidence_id": "current_total"}),) + steps[1:]
    else:
        child = steps[0].analysis_plan.model_copy(update={
            "top_n": 1} if change == "child" else {"metric_id": "item_quantity"})
        steps = (steps[0].model_copy(update={"analysis_plan": child}),) + steps[1:]
    forged = ComparativeExecutionPlan.model_construct(plan=compiled.plan, steps=steps)
    with pytest.raises(ValidationError):
        ComparativeExecutionPlan.model_validate(forged)


def reference(dataset, month, dimension="region"):
    orders = {o.order_id: o for o in dataset.orders
              if o.order_date.startswith(month) and o.status in ("paid", "completed")}
    products = {p.product_id: p for p in dataset.products}
    values = {}
    for item in dataset.order_items:
        if item.order_id in orders:
            key = (orders[item.order_id].order_region if dimension == "region"
                   else products[item.product_id].category)
            values[key] = values.get(key, 0) + item.quantity * item.unit_price_cents
    return values


@pytest.mark.parametrize("operation", ["compare", "mom", "contribution"])
def test_real_fixture(fixture_db, fixture_dataset, operation):
    result = run_comparative_analysis(plan(operation), fixture_db)
    assert result.error_code is None
    base = reference(fixture_dataset, "2024-01")
    current = reference(fixture_dataset, "2024-02")
    assert result.comparison.baseline == sum(base.values())
    assert result.comparison.current == sum(current.values())
    assert result.comparison.absolute_change == sum(current.values()) - sum(base.values())
    assert result.query_count == (4 if operation == "contribution" else 2)
    assert result.comparison.evidence_ids == ("baseline_total", "current_total")
    assert len({e.query_id for e in result.evidence}) == result.query_count
    assert not result.completeness.data_coverage_verified
    if operation == "contribution":
        for row in result.contribution.rows:
            assert row.dimension_change == current.get(row.dimension_value, 0) - base.get(row.dimension_value, 0)
            assert row.evidence_ids == ("baseline_breakdown", "current_breakdown")


def test_runner_order_evidence_and_shrinking_timeout(fixture_db):
    calls = []
    now = [0.0]
    def runner(child, db, limits):
        calls.append((child, limits.timeout_seconds))
        now[0] += 1
        return run_analysis_plan(child, db, limits)
    result = run_comparative_analysis(plan("contribution", top_n=1), fixture_db,
        ComparativeExecutionLimits(timeout_seconds=3.5), runner, clock=lambda: now[0])
    assert result.error_code == "timeout" and result.query_count == 4
    assert len(result.evidence) == 3
    assert [x[1] for x in calls] == sorted([x[1] for x in calls], reverse=True)
    assert [e.analysis_plan for e in result.evidence] == [c[0] for c in calls[:3]]


@pytest.mark.parametrize("fail_at", [1, 3])
@pytest.mark.parametrize("exception", [RuntimeError("secret/path bound=abc"), QueryError("db_error", "secret")])
def test_failure_counts_and_redaction(fixture_db, fail_at, exception):
    calls = []
    def runner(child, db, limits):
        calls.append(child)
        if len(calls) == fail_at:
            raise exception
        return run_analysis_plan(child, db, limits)
    result = run_comparative_analysis(plan("contribution"), fixture_db, runner=runner)
    assert result.query_count == len(calls) == fail_at
    assert len(result.evidence) == fail_at - 1
    assert result.error_code == "execution_error" and result.comparison is None
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("maximum", [1, 2, 3])
def test_budget(fixture_db, maximum):
    result = run_comparative_analysis(plan("contribution"), fixture_db,
                                     ComparativeExecutionLimits(max_queries=maximum))
    assert result.query_count == maximum and result.error_code == "budget_exhausted"
    assert result.comparison is None


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan"), "2"])
def test_invalid_deadline(timeout):
    limits = ComparativeExecutionLimits.model_construct(timeout_seconds=timeout)
    result = run_comparative_analysis(plan(), "unused", limits)
    assert result.error_code == "invalid_limits" and result.query_count == 0


def test_expired_before_first_call():
    ticks = iter([0.0, 31.0])
    result = run_comparative_analysis(plan(), "unused", clock=lambda: next(ticks))
    assert result.error_code == "timeout" and result.query_count == 0


@pytest.mark.parametrize("field,value,error", [
    ("metric_id", "item_quantity", "metadata_mismatch"),
    ("operation", "breakdown", "metadata_mismatch"),
    ("start_date", "2024-01-02", "metadata_mismatch"),
    ("end_date", "2024-01-30", "metadata_mismatch"),
    ("dimension_id", "category", "metadata_mismatch"),
    ("filters", ({"dimension_id": "region", "op": "eq"},), "metadata_mismatch"),
    ("unit", "元", "metadata_mismatch"),
    ("unit_code", "yuan", "metadata_mismatch"),
    ("semantic_version", "1.0.0", "metadata_mismatch"),
    ("query_id", "", "missing_query_id"),
    ("query_id", None, "missing_query_id"),
    ("query_id", "different", "metadata_mismatch"),
    ("rows", (), "invalid_result_shape"),
    ("rows", (ResultRow(dimension_value="华东", metric_value=1),), "invalid_result_shape"),
    ("rows", (ResultRow(metric_value=None),), "undefined_metric"),
    ("rows", (ResultRow(metric_value=float("nan")),), "invalid_result_shape"),
    ("rows", (ResultRow.model_construct(metric_value=True),), "invalid_result_shape"),
])
def test_bad_result(fixture_db, field, value, error):
    def runner(child, db, limits):
        return run_analysis_plan(child, db, limits).model_copy(update={field: value})
    result = run_comparative_analysis(plan(), fixture_db, runner=runner)
    assert result.error_code == error
    assert result.query_count == 1 and result.comparison is None and not result.evidence


@pytest.mark.parametrize("part,field,value,error", [
    ("execution", "status", "error", "execution_error"),
    ("execution", "truncated", True, "incomplete_result"),
    ("completeness", "truncated", True, "incomplete_result"),
    ("completeness", "is_complete_population", False, "incomplete_result"),
    ("completeness", "ranked_top_n", 1, "incomplete_result"),
    ("execution", "row_count", 2, "invalid_result_shape"),
    ("execution", "columns", ("wrong",), "invalid_result_shape"),
    ("execution", "query_id", "", "missing_query_id"),
])
def test_bad_nested(fixture_db, part, field, value, error):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        return r.model_copy(update={part: getattr(r, part).model_copy(update={field: value})})
    result = run_comparative_analysis(plan(), fixture_db, runner=runner)
    assert result.error_code == error and result.query_count == 1


@pytest.mark.parametrize("mode", ["duplicate", "null", "blank", "reconcile", "dimension"])
def test_bad_breakdown(fixture_db, mode):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        if child.operation != "breakdown":
            return r
        if mode == "dimension":
            return r.model_copy(update={"dimension_id": "category"})
        rows = r.rows
        if mode == "duplicate":
            rows = rows + (rows[0],)
        else:
            field = "metric_value" if mode == "reconcile" else "dimension_value"
            value = 1 if mode == "reconcile" else (None if mode == "null" else " ")
            rows = (rows[0].model_copy(update={field: value}),) + rows[1:]
        return r.model_copy(update={"rows": rows,
            "execution": r.execution.model_copy(update={"row_count": len(rows)})})
    r = run_comparative_analysis(plan("contribution", top_n=1), fixture_db, runner=runner)
    assert r.error_code == ("reconciliation_failed" if mode == "reconcile" else
                            "metadata_mismatch" if mode == "dimension" else "invalid_result_shape")
    assert r.comparison is None and r.contribution is None
    assert r.query_count == (4 if mode == "reconcile" else 3)


def test_empty_and_zero(fixture_db):
    source = plan("contribution", baseline_period=month_period(2020, 1), current_period=month_period(2020, 2))
    r = run_comparative_analysis(source, fixture_db)
    assert r.error_code is None and r.comparison.change_rate is None
    assert r.contribution.rows == () and r.comparison.current == 0
    r = run_comparative_analysis(plan(metric_id="aov_cents", baseline_period=month_period(2020, 1)), fixture_db)
    assert r.error_code == "undefined_metric" and r.comparison is None


def test_top_n_and_decimal_json(fixture_db):
    full = run_comparative_analysis(plan("contribution"), fixture_db)
    top = run_comparative_analysis(plan("contribution", top_n=1), fixture_db)
    assert full.error_code is top.error_code is None
    assert top.contribution.rows == full.contribution.rows[:1]
    assert top.contribution.hidden_dimension_count == len(full.contribution.rows) - 1
    assert top.contribution.hidden_net_change == sum(r.dimension_change for r in full.contribution.rows[1:])
    assert top.completeness.is_complete_population
    assert not top.completeness.display_is_complete_population
    assert [e.row_count for e in top.evidence] == [e.row_count for e in full.evidence]
    obj = json.loads(top.model_dump_json())
    assert obj["comparison"]["current"] == str(top.comparison.current)
    assert obj["contribution"]["hidden_net_change"] == str(top.contribution.hidden_net_change)
    assert isinstance(obj["contribution"]["rows"][0]["contribution_rate"], str)


@pytest.mark.parametrize("seed", [17, 83])
@pytest.mark.parametrize("dimension", ["region", "category"])
def test_generated_independent_reference(tmp_path, seed, dimension):
    from eda.data.generator import DemoDataSpec, generate_demo_dataset
    from eda.data.build_db import build_database
    dataset = generate_demo_dataset(DemoDataSpec(seed=seed, n_orders=80, n_customers=15,
        start_date="2024-01-01", end_date="2024-02-29"))
    db = tmp_path / "generated.db"
    build_database(dataset, db)
    baseline, current = reference(dataset, "2024-01", dimension), reference(dataset, "2024-02", dimension)
    r = run_comparative_analysis(plan("contribution", dimension_id=dimension), db)
    assert r.error_code is None
    delta = sum(current.values()) - sum(baseline.values())
    assert r.comparison.absolute_change == delta
    for row in r.contribution.rows:
        change = current.get(row.dimension_value, 0) - baseline.get(row.dimension_value, 0)
        assert row.dimension_change == change
        if delta:
            assert row.contribution_rate == (Decimal(change) / Decimal(delta)).quantize(
                Decimal("0.000000000001"), rounding="ROUND_HALF_UP")


def test_hand_calculated_fixture(fixture_db):
    result = run_comparative_analysis(plan("contribution", top_n=1), fixture_db)
    assert result.comparison.baseline == 223200
    assert result.comparison.current == 232000
    assert result.comparison.absolute_change == 8800
    row, = result.contribution.rows
    assert (row.dimension_value, row.baseline, row.current, row.dimension_change) == (
        "华东", 33300, 190000, 156700)
    assert result.contribution.hidden_dimension_count == 2
    assert result.contribution.hidden_net_change == -147900


@pytest.mark.parametrize("field,value", [("max_rows", 1), ("max_result_bytes", 1)])
def test_real_executor_truncation(fixture_db, field, value):
    from eda.sql.executor import ExecutionLimits
    query = ExecutionLimits.from_settings().model_copy(update={field: value})
    result = run_comparative_analysis(plan("contribution"), fixture_db,
        ComparativeExecutionLimits(query_limits=query))
    assert result.error_code == "incomplete_result" and result.comparison is None
    assert result.query_count == (3 if field == "max_rows" else 1)


def test_repeated_query_id(fixture_db):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        return r.model_copy(update={"query_id": "same-query",
            "execution": r.execution.model_copy(update={"query_id": "same-query"})})
    result = run_comparative_analysis(plan(), fixture_db, runner=runner)
    assert result.error_code == "metadata_mismatch" and result.query_count == 2
    assert len(result.evidence) == 1


@pytest.mark.parametrize("phase", ["return", "raise"])
def test_deadline_after_query(fixture_db, phase):
    now = [0.0]
    def runner(child, db, limits):
        now[0] = 31.0
        if phase == "raise":
            raise RuntimeError("private database path")
        return run_analysis_plan(child, db, limits)
    r = run_comparative_analysis(plan(), fixture_db, runner=runner, clock=lambda: now[0])
    assert r.error_code == "timeout" and r.query_count == 1
    assert not r.evidence and r.comparison is None


def test_filter_execution(fixture_db):
    from eda.plan.comparative import ComparativeFilter
    source = plan("contribution", filters=(
        ComparativeFilter(dimension_id="region", op="in", value=("华东", "西南")),))
    r = run_comparative_analysis(source, fixture_db)
    assert r.error_code is None
    assert r.comparison.baseline == 33300 and r.comparison.current == 232000
    assert all(e.analysis_plan.filters[0].values() == ("华东", "西南") for e in r.evidence)


def test_total_extra_row(fixture_db):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        return r.model_copy(update={"rows": r.rows * 2,
            "execution": r.execution.model_copy(update={"row_count": 2})})
    r = run_comparative_analysis(plan(), fixture_db, runner=runner)
    assert r.error_code == "invalid_result_shape" and r.query_count == 1


@pytest.mark.parametrize("changes", [{"max_queries": 5}, {"max_queries": True},
                                    {"query_limits": {"timeout_seconds": float("inf")}}])
def test_forged_limits(changes):
    limits = ComparativeExecutionLimits().model_copy(update=changes)
    r = run_comparative_analysis(plan(), "unused", limits)
    assert r.error_code == "invalid_limits" and r.query_count == 0


def test_zero_total_change_has_no_contribution_rates(fixture_db):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        # A consistent zero-valued population, including nonempty breakdowns.
        return r.model_copy(update={"rows": tuple(row.model_copy(update={"metric_value": 0}) for row in r.rows)})
    r = run_comparative_analysis(plan("contribution"), fixture_db, runner=runner)
    assert r.error_code is None and r.comparison.change_rate is None
    assert all(row.contribution_rate is None for row in r.contribution.rows)


@pytest.mark.parametrize("stop_at", [1, 2, 3, 4])
def test_incomplete_never_reaches_calculation(fixture_db, monkeypatch, stop_at):
    import eda.query.comparative as service
    def forbidden(*args, **kwargs):
        pytest.fail("incomplete results must not reach arithmetic")
    monkeypatch.setattr(service, "calculate_comparison", forbidden)
    monkeypatch.setattr(service, "calculate_contribution", forbidden)
    calls = []
    def runner(child, db, limits):
        calls.append(child)
        r = run_analysis_plan(child, db, limits)
        if len(calls) == stop_at:
            r = r.model_copy(update={"completeness": r.completeness.model_copy(
                update={"is_complete_population": False})})
        return r
    r = run_comparative_analysis(plan("contribution"), fixture_db, runner=runner)
    assert r.error_code == "incomplete_result"
    assert r.query_count == len(calls) == stop_at
    assert len(r.evidence) == stop_at - 1
    assert r.comparison is r.contribution is None
    assert not r.completeness.is_complete_population
    assert not r.completeness.display_is_complete_population


def test_single_shared_deadline(fixture_db, monkeypatch):
    import eda.query.comparative as service
    original = service.ComparativeExecutionBudget
    budgets, observed = [], []
    now = [10.0]
    def create(*args, **kwargs):
        budget = original(*args, **kwargs)
        budgets.append(budget)
        return budget
    monkeypatch.setattr(service, "ComparativeExecutionBudget", create)
    def runner(child, db, limits):
        observed.append((budgets[0].deadline, budgets[0].query_count, limits.timeout_seconds))
        now[0] += 1.0
        return run_analysis_plan(child, db, limits)
    r = run_comparative_analysis(plan("contribution"), fixture_db,
        ComparativeExecutionLimits(timeout_seconds=5.0), runner, clock=lambda: now[0])
    assert r.error_code is None and len(budgets) == 1
    assert [x[:2] for x in observed] == [(15.0, n) for n in range(1, 5)]
    assert all(x[2] <= 6 - n for n, x in enumerate(observed, 1))
    assert budgets[0].query_count == 4


def test_actual_evidence_mapping(fixture_db):
    returned = []
    def runner(child, db, limits):
        result = run_analysis_plan(child, db, limits)
        returned.append(result)
        return result
    result = run_comparative_analysis(plan("contribution"), fixture_db, runner=runner)
    assert result.error_code is None
    assert [e.query_id for e in result.evidence] == [r.query_id for r in returned]
    assert [e.role for e in result.evidence] == [
        "baseline_total", "current_total", "baseline_breakdown", "current_breakdown"]
    assert all(e.evidence_id == e.role for e in result.evidence)


def test_json_large_integer_and_decimal_fraction(fixture_db):
    base = 9007199254740993
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        value = base if child.start_date == "2024-01-01" else base + 1
        return r.model_copy(update={"rows": (ResultRow(metric_value=value),)})
    r = run_comparative_analysis(plan(), fixture_db, runner=runner)
    payload = json.loads(r.model_dump_json())
    assert payload["comparison"]["baseline"] == "9007199254740993"
    assert payload["comparison"]["current"] == "9007199254740994"
    assert payload["comparison"]["absolute_change"] == "1"
    assert r.model_dump(mode="json") == payload
    assert r.model_dump_json() == r.model_dump_json()

    def fraction_runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        value = 0.1 if child.start_date == "2024-01-01" else 0.3
        return r.model_copy(update={"rows": (ResultRow(metric_value=value),)})
    r = run_comparative_analysis(plan(metric_id="aov_cents"), fixture_db, runner=fraction_runner)
    assert r.comparison.absolute_change == Decimal("0.2")
    assert json.loads(r.model_dump_json())["comparison"]["absolute_change"] == "0.2"


@pytest.mark.parametrize("mode", ["empty", "null"])
def test_breakdown_empty_or_null_not_silently_zero(fixture_db, mode):
    def runner(child, db, limits):
        r = run_analysis_plan(child, db, limits)
        if child.operation == "total":
            return r
        rows = () if mode == "empty" else (r.rows[0].model_copy(update={"metric_value": None}),)
        return r.model_copy(update={"rows": rows,
            "execution": r.execution.model_copy(update={"row_count": len(rows)})})
    r = run_comparative_analysis(plan("contribution"), fixture_db, runner=runner)
    assert r.error_code == ("reconciliation_failed" if mode == "empty" else "undefined_metric")
    assert r.comparison is r.contribution is None
