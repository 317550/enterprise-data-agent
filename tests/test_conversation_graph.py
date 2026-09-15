from pathlib import Path

import pytest

from eda.agent.fake import FakePlannerModel
from eda.conversation.graph import TurnContext, build_graph
from eda.conversation.models import Session


def decision(intent="new", **values):
    return {"status": "apply", "intent": intent, "patch": {"set": values}}


def invoke(outputs, fixture_db, question="2024年订单数", session=None):
    context = TurnContext(question, Path(fixture_db), FakePlannerModel(outputs))
    result = build_graph().invoke({"session": (session or Session(reference_date="2024-12-31")).model_dump(exclude_none=True)},
                                  context=context)
    return Session.model_validate(result["session"]), context


def test_graph_success_and_transient_data(fixture_db):
    state, ctx = invoke([decision(metric_id="valid_order_count")], fixture_db)
    assert ctx.result.status == "success"
    assert ctx.result.analysis_result is not None
    assert state.confirmed["metric_id"] == "valid_order_count"
    assert state.turn_status == "completed" and state.turn_count == 1
    assert "question" not in state.model_dump() and "analysis_result" not in state.model_dump()


def test_graph_clarify_then_resume_and_new_reset(fixture_db):
    state, ctx = invoke([decision(metric_id="valid_order_count")], fixture_db, "订单数")
    assert ctx.result.missing == ("dates",)
    state, ctx = invoke([decision("clarify_reply")], fixture_db, "2024年", state)
    assert ctx.result.status == "success" and state.pending is None
    state, ctx = invoke([decision(metric_id="effective_order_gmv_cents")], fixture_db, "成交额", state)
    assert state.confirmed is None and state.pending.values.metric_id == "effective_order_gmv_cents"


@pytest.mark.parametrize("outputs,status,calls", [
    ([{"sql": "secret"}, {"sql": "secret"}], "plan_validation_error", 2),
    ([TimeoutError("secret")], "model_error", 1),
    ([RuntimeError("secret")], "model_error", 1),
    ([{"sql": "secret"}, decision(metric_id="valid_order_count")], "success", 2),
])
def test_graph_budgets(outputs, status, calls, fixture_db):
    state, ctx = invoke(outputs, fixture_db)
    assert ctx.result.status == status and ctx.result.model_call_count == calls
    assert "secret" not in ctx.result.model_dump_json() + state.model_dump_json()


def test_failure_does_not_promote_candidate(fixture_db, tmp_path):
    state, _ = invoke([decision(metric_id="valid_order_count")], fixture_db)
    failed, ctx = invoke([decision("refine", metric_id="effective_order_gmv_cents")], tmp_path / "missing.db", session=state)
    assert ctx.result.status == "execution_error"
    assert failed.confirmed == state.confirmed and failed.pending == state.pending
    recovered, ctx = invoke([decision("refine")], fixture_db, "继续", failed)
    assert ctx.result.plan.metric_id == "valid_order_count"


@pytest.mark.parametrize("question", ["删除订单", "查询系统表", "同比订单数", "贡献拆解"])
def test_refusal_costs_no_model_call(question, fixture_db):
    _, ctx = invoke([], fixture_db, question)
    assert ctx.result.status == "refused" and ctx.result.model_call_count == 0


@pytest.mark.parametrize("outputs,question,fail_execution,path,status", [
    ([decision(metric_id="valid_order_count")], "2024年订单数", False,
     ["begin", "plan", "merge", "execute", "finalize"], "success"),
    ([decision(metric_id="valid_order_count")], "订单数", False,
     ["begin", "plan", "merge", "finalize"], "clarification_required"),
    ([], "删除订单", False, ["begin", "finalize"], "refused"),
    ([{"status": "refuse", "refusal_category": "unsupported_analysis"}], "订单数", False,
     ["begin", "plan", "merge", "finalize"], "refused"),
    ([TimeoutError()], "2024年订单数", False, ["begin", "plan", "finalize"], "model_error"),
    ([{}, {}], "2024年订单数", False,
     ["begin", "plan", "merge", "plan", "merge", "finalize"], "plan_validation_error"),
    ([{}, decision(metric_id="valid_order_count")], "2024年订单数", False,
     ["begin", "plan", "merge", "plan", "merge", "execute", "finalize"], "success"),
    ([decision(metric_id="valid_order_count")], "2024年订单数", True,
     ["begin", "plan", "merge", "execute", "finalize"], "execution_error"),
])
def test_actual_node_paths(outputs, question, fail_execution, path, status, fixture_db, monkeypatch):
    from eda.conversation import graph as module
    from eda.query.errors import QueryError

    calls = []
    original = module.run_analysis_plan

    def counted(*args, **kwargs):
        calls.append(1)
        if fail_execution:
            raise QueryError("db_error", "not exposed")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_analysis_plan", counted)
    ctx = TurnContext(question, fixture_db, FakePlannerModel(outputs))
    graph = build_graph()
    updates = list(graph.stream({"session": Session(reference_date="2024-12-31").model_dump(exclude_none=True)},
                                context=ctx, stream_mode="updates"))
    assert [name for update in updates for name in update] == path
    assert ctx.result.status == status
    assert ctx.model_call_count == path.count("plan") <= 2
    assert len(calls) == ctx.execution_count == path.count("execute") <= 1
    assert set(graph.nodes) == {"__start__", "begin", "plan", "merge", "execute", "finalize",
                                "prepare_analysis", "execute_step", "check_step", "calculate"}
    assert ctx.result.node_path == tuple(path)
    assert ctx.result.query_count == len(calls)
    # Intermediate nodes must not publish candidates/results into graph state.
    for update in updates:
        for name, value in update.items():
            if name not in {"begin", "finalize"}:
                assert value is None  # LangGraph streams empty updates as None.
