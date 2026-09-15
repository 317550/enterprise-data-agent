"""Offline 4B-3 protocol, graph, recovery, budgets, and independent results."""

from decimal import Decimal
import json
import logging
import os
import socket
import subprocess
import sys

import pytest

from eda.agent.models import DecisionError
from eda.conversation.checkpoint import ConversationError, open_checkpointer
from eda.conversation.fake import FakeConversationModel
from eda.conversation.graph import TurnContext, build_graph
from eda.conversation.models import Session, TurnDecision, Values, Patch, parse_turn
from eda.conversation.service import ConversationService
from eda.plan.comparative import month_period
from eda.query.service import run_analysis_plan

GMV = "effective_order_gmv_cents"
MONTHS = "对比2024年2月和2024年1月的成交额"
YEARS = "对比2024年和2023年的有效订单成交额。"
DRILL = "按地区看变化贡献。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("network is forbidden")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def apply(intent="new", **values):
    return {"status": "apply", "intent": intent, "patch": {"set": values}}


def service(db, tmp_path, model=None, **kwargs):
    return ConversationService(db, tmp_path / "cp.db", model=model or FakeConversationModel(),
                               reference_date="2024-12-31", **kwargs)


def invoke(db, question=MONTHS, outputs=None, state=None, **kwargs):
    ctx = TurnContext(question, db, FakeConversationModel(outputs), **kwargs)
    graph = build_graph()
    updates = list(graph.stream({"session": (state or Session(reference_date="2024-12-31")).model_dump(exclude_none=True)},
        {"recursion_limit": 20}, context=ctx, stream_mode="updates"))
    session = Session.model_validate(updates[-1]["finalize"]["session"])
    return ctx.result, session, ctx, updates


@pytest.mark.parametrize("question,operation,count,start,end", [
    (YEARS, "compare", 2, "2024-01-01", "2024-12-31"),
    (MONTHS, "compare", 2, "2024-02-01", "2024-02-29"),
    ("比较2024年2月和2024年1月的订单数。", "compare", 2, "2024-02-01", "2024-02-29"),
    ("查看2024年2月成交额环比。", "mom", 2, "2024-02-01", "2024-02-29"),
    ("查看2024年1月成交额环比。", "mom", 2, "2024-01-01", "2024-01-31"),
    ("查看2024年3月成交额环比。", "mom", 2, "2024-03-01", "2024-03-31"),
    (MONTHS + "，并按地区看变化贡献。", "contribution", 4, "2024-02-01", "2024-02-29"),
])
def test_operations_and_observed_step_path(fixture_db, question, operation, count, start, end):
    result, state, ctx, updates = invoke(fixture_db, question)
    assert result.status == "success"
    path = ["begin", "plan", "merge", "prepare_analysis"] + ["execute_step", "check_step"] * count + ["calculate", "finalize"]
    assert [name for update in updates for name in update] == path == list(result.node_path)
    assert result.model_call_count == 1 and result.query_count == count
    assert result.analysis_operation == operation
    assert (result.current_period.start_date, result.current_period.end_date) == (start, end)
    assert result.baseline_period.end_date < start
    assert result.completeness.calendar_period_complete and not result.completeness.data_coverage_verified
    assert state.confirmed_type == "comparative" and ctx.comparative.index == count
    assert len({e.query_id for e in result.evidence}) == count
    for update in updates[1:-1]:
        assert next(iter(update.values())) is None
    if operation == "mom" and start == "2024-01-01":
        assert result.baseline_period.start_date == "2023-12-01"
    if operation == "mom" and start == "2024-03-01":
        assert result.baseline_period.end_date == "2024-02-29"


@pytest.mark.parametrize("question", [
    "对比2024年和2024年2月的成交额", "对比2024年和2024年的成交额",
    "对比2024年2月和2024年13月的成交额", "对比2024年成交额",
    "对比最近两年的成交额", "对比2024年2月1日和2024年1月1日的成交额",
    "对比2024年和2023年的成交额，以2024年为基期", "对比2024年和2023年的成交额反向计算",
    "对比2024年和2023年的成交额，按地区和类别看贡献",
    "对比2024年和2023年的成交额，按渠道看贡献",
    "对比2024年和2023年的客单价，按地区看贡献",
    "对比2024年和2023年的订单数，按类别看贡献",
])
def test_bad_user_requests_never_query(fixture_db, question):
    result, _, _, _ = invoke(fixture_db, question)
    assert result.status == "clarification_required"
    assert result.query_count == 0 and result.model_call_count == 1
    assert result.plan is None and result.comparison is None and not result.evidence


def test_unfinished_current_month_never_falls_back(fixture_db):
    state = Session(reference_date="2024-02-20")
    r, state, _, _ = invoke(fixture_db, "查看本月成交额环比", state=state)
    assert r.status == "clarification_required" and "dates" in r.missing
    assert state.pending.values.current_period is None and r.query_count == 0


@pytest.mark.parametrize("values", [
    {"sql": "SELECT 1"}, {"formula": "a/b"}, {"dimension_id": ["region", "category"]},
    {"current_period": None}, {"operation": "join"}, {"metric_id": "orders"},
    {"top_n": True}, {"top_n": "2"}, {"query_id": "forged"}, {"evidence_id": "forged"},
    {"filters": [{"dimension_id": "region", "op": "eq", "value": "unknown"}]},
])
def test_strict_protocol(values):
    with pytest.raises(DecisionError):
        parse_turn(apply(**values))


@pytest.mark.parametrize("raw", [
    '{"status":"apply","status":"refuse"}',
    {"status": "apply", "intent": "new", "missing": []},
    {"status": "apply", "intent": "new", "refusal_category": None},
    {"status": "refuse", "refusal_category": "write_request", "patch": {}},
    TurnDecision.model_construct(status="apply", intent="new", sql="secret"),
    TurnDecision(status="apply", intent="new").model_copy(update={"query_id": "secret"}),
    TurnDecision(status="apply", intent="new").model_copy(update={"patch": Patch.model_construct(
        set=Values.model_construct(metric_id=GMV, top_n=True))}),
])
def test_raw_and_instance_bypass(raw):
    with pytest.raises(DecisionError):
        parse_turn(raw)


@pytest.mark.parametrize("outputs,status,calls", [
    ([{}, apply(metric_id=GMV, operation="compare")], "success", 2),
    ([{}, {}, apply(metric_id=GMV, operation="compare")], "plan_validation_error", 2),
    ([{"status": "clarify", "intent": "new", "missing": ["dates"], "patch": {"set": {"operation": "compare"}}}], "clarification_required", 1),
    ([{"status": "refuse", "refusal_category": "unsupported_analysis"}], "refused", 1),
    ([TimeoutError("private")], "model_error", 1),
    ([RuntimeError("private")], "model_error", 1),
])
def test_repair_and_terminal_budgets(fixture_db, outputs, status, calls):
    r, _, _, _ = invoke(fixture_db, outputs=outputs)
    assert (r.status, r.model_call_count) == (status, calls)
    assert r.query_count == (2 if status == "success" else 0)
    assert "private" not in r.model_dump_json()


@pytest.mark.parametrize("operation,question,values", [
    ("mom", "查看2024年2月成交额环比", {"baseline_period": month_period(2024, 1).model_dump()}),
    ("compare", MONTHS, {"current_period": month_period(2024, 1).model_dump(), "baseline_period": month_period(2024, 2).model_dump()}),
    ("compare", MONTHS, {"current_period": month_period(2024, 2).model_copy(update={"label": "PRIVATE_LABEL"}).model_dump()}),
])
def test_model_cannot_set_direction_or_mom_baseline(fixture_db, operation, question, values):
    raw = apply(metric_id=GMV, operation=operation, **values)
    r, state, _, _ = invoke(fixture_db, question, [raw, raw])
    assert r.status == "plan_validation_error" and r.query_count == 0
    assert state.confirmed is None and "PRIVATE_LABEL" not in r.model_dump_json()


@pytest.mark.parametrize("fail_at,mode", [(1, "raise"), (3, "raise"), (1, "truncate"), (3, "truncate")])
def test_query_failure_counts_and_stops(fixture_db, monkeypatch, fail_at, mode):
    calls = []
    def runner(plan, db, limits):
        calls.append(plan)
        if len(calls) == fail_at and mode == "raise":
            raise RuntimeError("PRIVATE_PATH BINDING_VALUE")
        r = run_analysis_plan(plan, db, limits)
        if len(calls) == fail_at:
            return r.model_copy(update={"execution": r.execution.model_copy(update={"truncated": True})})
        return r
    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan", runner)
    r, state, ctx, _ = invoke(fixture_db, MONTHS + "并按地区看变化贡献")
    assert r.status == "execution_error" and r.query_count == len(calls) == fail_at
    assert r.error_code == ("execution_error" if mode == "raise" else "incomplete_result")
    assert r.model_call_count == 1 and "calculate" not in r.node_path
    assert r.comparison is r.contribution is r.plan is None and not r.evidence
    assert state.confirmed is None and len(ctx.comparative.evidence) == fail_at - 1
    assert "PRIVATE_PATH" not in r.model_dump_json()


def test_each_step_executes_once_and_evidence_matches(fixture_db, monkeypatch):
    returned = []
    def runner(plan, db, limits):
        result = run_analysis_plan(plan, db, limits)
        returned.append(result)
        return result
    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan", runner)
    ctx = TurnContext(MONTHS + "并按地区看贡献", fixture_db, FakeConversationModel())
    previous = 0
    for update in build_graph().stream({"session": Session(reference_date="2024-12-31").model_dump(exclude_none=True)}, context=ctx):
        assert len(returned) - previous <= 1
        previous = len(returned)
    assert previous == 4
    assert [e.query_id for e in ctx.result.evidence] == [r.query_id for r in returned]
    assert [e.evidence_id for e in ctx.result.evidence] == ["baseline_total", "current_total", "baseline_breakdown", "current_breakdown"]
    edges = build_graph().get_graph().edges
    assert {e.source for e in edges if e.target == "__end__"} == {"finalize"}


def test_one_deadline_includes_planning_and_every_query(fixture_db, monkeypatch):
    now, timeouts = [10.0], []
    class Model(FakeConversationModel):
        def plan(self, question, context):
            assert context["remaining_seconds"] <= 5
            now[0] += 2
            return super().plan(question, context)
    def runner(plan, db, limits):
        timeouts.append(limits.timeout_seconds)
        now[0] += 1
        return run_analysis_plan(plan, db, limits)
    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan", runner)
    ctx = TurnContext(MONTHS + "并按地区看贡献", fixture_db, Model(), timeout_seconds=5.0, clock=lambda: now[0])
    build_graph().invoke({"session": Session(reference_date="2024-12-31").model_dump(exclude_none=True)}, context=ctx)
    assert ctx.deadline == ctx.comparative.budget.deadline == 15
    assert ctx.result.error_code == "timeout" and ctx.result.query_count == 3
    assert ctx.result.model_call_count == 1 and timeouts == [3, 2, 1]


def test_planning_timeout_is_terminal_and_zero_query(fixture_db):
    now = [0.0]
    class Model(FakeConversationModel):
        def plan(self, question, context):
            now[0] = 6.0
            return {}
    ctx = TurnContext(MONTHS, fixture_db, Model(), timeout_seconds=5.0, clock=lambda: now[0])
    build_graph().invoke({"session": Session(reference_date="2024-12-31").model_dump(exclude_none=True)}, context=ctx)
    assert ctx.result.error_code == "timeout"
    assert ctx.result.model_call_count == 1 and ctx.result.query_count == 0


def test_restart_inherit_drill_replace_and_new_topic(fixture_db, tmp_path):
    first = service(fixture_db, tmp_path)
    base = first.run("a", YEARS)
    assert base.status == "success"
    second = service(fixture_db, tmp_path)
    region = second.run("a", DRILL)
    assert region.status == "success" and region.plan.dimension_id == "region"
    assert {"metric_id", "current_period", "baseline_period"} <= set(region.inherited)
    category = service(fixture_db, tmp_path).run("a", "改按类别看贡献。")
    assert category.plan.dimension_id == "category" and category.query_count == 4
    assert category.comparison == base.comparison and category.changed == ("dimension_id",)
    assert service(fixture_db, tmp_path).run("b", DRILL).query_count == 0
    fresh = second.run("a", "2024年订单数", new_topic=True)
    assert fresh.status == "success" and fresh.query_count == 1
    assert fresh.current_period is None and fresh.plan.filters == ()
    assert second.inspect("a").confirmed_type == "single"
    assert second.run("a", DRILL).query_count == 0


def test_comparative_pending_and_new_topic_clarification(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path)
    svc.run("a", YEARS)
    assert svc.run("a", "订单数", new_topic=True).missing == ("dates",)
    state = svc.inspect("a")
    assert state.confirmed is None and state.pending.analysis_type == "single"
    assert svc.run("a", "2024年").status == "success"
    assert svc.run("a", "对比2024年成交额", new_topic=True).status == "clarification_required"
    assert svc.inspect("a").pending.analysis_type == "comparative"
    assert svc.run("a", "2024年和2023年").status == "success"


def test_invalid_date_clears_pending_periods_and_no_auto_drill(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path)
    svc.run("a", YEARS)
    assert svc.run("a", DRILL).status == "success"
    r = svc.run("a", "继续")
    assert r.status == "clarification_required" and r.query_count == 0
    r = svc.run("a", "对比2024年13月和2024年1月成交额")
    assert r.query_count == 0
    assert svc.inspect("a").pending.values.current_period is None


def test_failed_drill_does_not_promote_and_filters_persist(fixture_db, tmp_path, monkeypatch):
    svc = service(fixture_db, tmp_path)
    assert svc.run("a", "对比2024年和2023年的华东成交额").status == "success"
    before = svc.inspect("a")
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_FAILURE")
    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan", fail)
    r = svc.run("a", DRILL)
    assert r.status == "execution_error" and r.query_count == 1
    assert svc.inspect("a").confirmed == before.confirmed
    assert before.confirmed["filters"][0]["value"] == "华东"
    r = svc.run("a", "2024年订单数", new_topic=True)
    assert r.status == "execution_error" and svc.inspect("a").confirmed == before.confirmed


@pytest.mark.parametrize("version,value", [("state_version", "conversation-v1"), ("semantic_version", "1.0.0")])
def test_old_checkpoint_rejected_without_migration(fixture_db, tmp_path, version, value):
    svc = service(fixture_db, tmp_path)
    raw = Session(reference_date="2024-12-31").model_dump(exclude_none=True)
    raw[version] = value
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        build_graph(saver).update_state({"configurable": {"thread_id": "old"}}, {"session": raw})
    before = svc.checkpoint_db_path.read_bytes()
    with pytest.raises(ConversationError, match="incompatible_checkpoint"):
        svc.run("old", YEARS)
    assert svc.checkpoint_db_path.read_bytes() == before


def test_checkpoint_and_logs_only_business_whitelist(fixture_db, tmp_path, caplog):
    raw = apply(metric_id=GMV, operation="contribution", dimension_id="region",
                filters=[{"dimension_id": "region", "op": "eq", "value": "华东"}])
    svc = service(fixture_db, tmp_path, FakeConversationModel([raw]))
    with caplog.at_level(logging.DEBUG):
        r = svc.run("a", MONTHS + "按地区看贡献 PRIVATE_QUESTION")
    assert r.status == "success"
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        saved = json.dumps([item._asdict() for item in saver.list({"configurable": {"thread_id": "a"}})], ensure_ascii=False, default=str)
    for text in ("PRIVATE_QUESTION", r.request_id, "comparison", "evidence", "raw_response", "decision_schema", "deadline", "query_count", "SQL", "DEEPSEEK_API_KEY"):
        assert text not in saved + caplog.text
    for e in r.evidence:
        assert e.query_id not in saved + caplog.text
    assert "华东" in saved and "华东" not in caplog.text
    state = svc.inspect("a").model_dump(exclude_none=True)
    assert set(state) == {"state_version", "semantic_version", "reference_date", "confirmed", "confirmed_type", "turn_count", "turn_status"}
    assert set(state["confirmed"]) == {"metric_id", "operation", "current_period", "baseline_period", "dimension_id", "filters", "sort_direction"}


@pytest.mark.parametrize("question", ["删除订单并对比2024年和2023年", "对比系统表", "SELECT 1 对比", "为什么成交额下降", "预测2025年成交额"])
def test_security_zero_queries(fixture_db, question):
    r, _, _, _ = invoke(fixture_db, question, [])
    assert r.status == "refused" and r.query_count == r.model_call_count == 0
    if "为什么" in question:
        assert r.refusal_reason == "无法判断原因"


@pytest.mark.parametrize("seed", [17, 83])
@pytest.mark.parametrize("dimension", ["地区", "类别"])
def test_independent_generated_values(tmp_path, seed, dimension):
    from eda.data.generator import DemoDataSpec, generate_demo_dataset
    from eda.data.build_db import build_database
    dataset = generate_demo_dataset(DemoDataSpec(seed=seed, n_orders=80, n_customers=15,
        start_date="2024-01-01", end_date="2024-02-29"))
    db = tmp_path / "generated.db"
    build_database(dataset, db)
    orders = {o.order_id: o for o in dataset.orders if o.status in {"paid", "completed"}}
    products = {p.product_id: p for p in dataset.products}
    totals = {"2024-01": {}, "2024-02": {}}
    for item in dataset.order_items:
        if item.order_id in orders:
            order = orders[item.order_id]
            key = order.order_region if dimension == "地区" else products[item.product_id].category
            bucket = totals[order.order_date[:7]]
            bucket[key] = bucket.get(key, 0) + item.quantity * item.unit_price_cents
    r, _, _, _ = invoke(db, MONTHS + f"并按{dimension}看贡献")
    assert r.status == "success"
    baseline, current = totals.values()
    delta = sum(current.values()) - sum(baseline.values())
    assert r.comparison.baseline == sum(baseline.values()) and r.comparison.current == sum(current.values())
    assert r.comparison.absolute_change == delta
    for row in r.contribution.rows:
        change = current.get(row.dimension_value, 0) - baseline.get(row.dimension_value, 0)
        assert row.dimension_change == change
        assert row.contribution_rate == (Decimal(change) / Decimal(delta)).quantize(Decimal("0.000000000001"), rounding="ROUND_HALF_UP")


def test_fixture_top_n_zero_and_json(fixture_db):
    r, _, _, _ = invoke(fixture_db, MONTHS + "并按地区看贡献前1")
    assert r.status == "success" and r.comparison.absolute_change == 8800
    assert r.contribution.hidden_dimension_count == 2 and r.contribution.hidden_net_change == -147900
    assert r.evidence[2].analysis_plan.top_n is None
    assert json.loads(r.model_dump_json())["comparison"]["current"] == "232000"
    zero, _, _, _ = invoke(fixture_db, "对比2020年2月和2020年1月的成交额，并按地区看贡献")
    assert zero.status == "success" and zero.contribution.rows == ()
    assert zero.comparison.change_rate is None
    assert json.loads(zero.model_dump_json())["comparison"]["change_rate"] is None


def test_fake_cli_five_separate_processes(fixture_db, tmp_path):
    # Each child independently disables networking before entering the real CLI.
    script = "import socket,runpy; socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('offline')); runpy.run_module('eda.conversation.cli',run_name='__main__')"
    base = [sys.executable, "-c", script, "--thread", "demo", "--db", str(fixture_db),
            "--checkpoint-db", str(tmp_path / "cli.db"), "--reference-date", "2024-12-31"]
    for question, count, flags in [(YEARS, 2, ["--new-topic"]), ("查看2024年2月成交额环比", 2, ["--new-topic"]),
            (DRILL, 4, []), ("改按类别看贡献", 4, []), ("2024年订单数", 1, ["--new-topic"])]:
        child = subprocess.run([*base, question, *flags], capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30)
        assert child.returncode == 0, child.stdout + child.stderr
        result = json.loads(child.stdout)
        assert result["query_count"] == count and result["model_call_count"] == 1
        if count == 1:
            assert "current_period" not in result


@pytest.mark.parametrize("extra", [
    {"metric_id": "valid_order_count"},
    {"filters": [{"dimension_id": "region", "op": "eq", "value": "华北"}]},
    {"current_period": month_period(2024, 3).model_dump()},
    {"dimension_id": "category"},
])
def test_drill_cannot_silently_change_context(fixture_db, tmp_path, extra):
    svc = service(fixture_db, tmp_path)
    assert svc.run("a", MONTHS).status == "success"
    before = svc.inspect("a")
    raw = apply("refine", **({"operation": "contribution", "dimension_id": "region"} | extra))
    svc.model = FakeConversationModel([raw, raw])
    r = svc.run("a", DRILL)
    assert r.status == "plan_validation_error" and r.query_count == 0
    assert svc.inspect("a").confirmed == before.confirmed


def test_comparative_recovery_after_mid_query_checkpoint(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path)
    svc.run("a", MONTHS)
    before = svc.inspect("a")
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        ctx = TurnContext(DRILL, fixture_db, FakeConversationModel())
        graph = build_graph(saver)
        graph.invoke({"session": before.model_dump(exclude_none=True)}, {"configurable": {"thread_id": "a"}},
            context=ctx, interrupt_after=["check_step"], durability="sync")
        assert ctx.comparative.budget.query_count == 1
        recovered = Session.model_validate(graph.get_state({"configurable": {"thread_id": "a"}}).values["session"])
        assert recovered.confirmed == before.confirmed and recovered.pending == before.pending
        assert recovered.turn_status == "in_progress" and recovered.turn_count == before.turn_count
    r = service(fixture_db, tmp_path).run("a", DRILL)
    assert r.status == "success" and r.recovered and r.query_count == 4 and r.model_call_count == 1


def test_maximal_repair_path_is_bounded(fixture_db):
    raw = apply(metric_id=GMV, operation="contribution", dimension_id="region")
    r, _, _, _ = invoke(fixture_db, MONTHS + "按地区看贡献", [{}, raw])
    assert r.status == "success" and r.query_count == 4 and r.model_call_count == 2
    assert len(r.node_path) == 16 and r.node_path.count("execute_step") == 4


def test_new_topic_model_failure_preserves_comparison(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path)
    svc.run("a", YEARS)
    before = svc.inspect("a")
    svc.model = FakeConversationModel([TimeoutError("PRIVATE_MODEL")])
    r = svc.run("a", "2024年订单数", new_topic=True)
    assert r.status == "model_error" and r.query_count == 0
    assert svc.inspect("a").confirmed == before.confirmed


def test_cli_zero_numeric_is_null(fixture_db, tmp_path, capsys):
    from eda.conversation.cli import main
    assert main(["对比2020年2月和2020年1月成交额", "--thread", "zero", "--db", str(fixture_db),
        "--checkpoint-db", str(tmp_path / "zero.db"), "--reference-date", "2024-12-31"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparison"]["change_rate"] is None


def test_construct_cannot_hide_fields_using_fields_set():
    hidden = Values.model_construct(_fields_set={"metric_id"}, metric_id=GMV, top_n=True)
    raw = TurnDecision.model_construct(status="apply", intent="new", patch=Patch.model_construct(set=hidden))
    with pytest.raises(DecisionError):
        parse_turn(raw)


@pytest.mark.parametrize("suffix", ["，按上周", "，按周末", "，合并两个视角求和"])
def test_unresolved_user_scope_never_uses_history(fixture_db, tmp_path, suffix):
    svc = service(fixture_db, tmp_path)
    svc.run("a", MONTHS)
    raw = apply("refine", operation="contribution", dimension_id="region")
    svc.model = FakeConversationModel([raw])
    r = svc.run("a", "按地区看贡献" + suffix)
    assert r.status == "clarification_required" and r.query_count == 0 and r.model_call_count == 1


@pytest.mark.parametrize("values", [
    {"operation": "compare", "current_period": month_period(2024, 1), "baseline_period": month_period(2024, 2)},
    {"operation": "mom", "current_period": month_period(2024, 3), "baseline_period": month_period(2024, 1)},
    {"operation": "compare", "dimension_id": "region"},
    {"operation": "contribution", "metric_id": "aov_cents", "dimension_id": "region"},
])
def test_corrupt_comparative_draft_is_rejected(values):
    with pytest.raises(ValueError):
        Session(reference_date="2024-12-31", pending={"analysis_type": "comparative", "values": values, "missing": ["metric_id"]})
