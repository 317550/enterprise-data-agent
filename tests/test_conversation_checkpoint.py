import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from eda.agent.fake import FakePlannerModel
from eda.conversation.checkpoint import ConversationError, open_checkpointer, thread_lock
from eda.conversation.graph import TurnContext, build_graph
from eda.conversation.models import Session
from eda.conversation.service import ConversationService


def apply(intent="new", **values):
    return {"status": "apply", "intent": intent, "patch": {"set": values}}


def service(fixture_db, tmp_path, outputs=(), **kwargs):
    return ConversationService(fixture_db, tmp_path / "checkpoints.db", model=FakePlannerModel(outputs), **kwargs)


def test_restart_clarification_and_thread_isolation(fixture_db, tmp_path):
    first = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")], reference_date="2024-12-31")
    assert first.run("a", "订单数").status == "clarification_required"
    second = service(fixture_db, tmp_path, [apply("clarify_reply"), apply("refine")])
    assert second.run("a", "2024年").status == "success"
    assert second.run("b", "继续").missing == ("history", "metric_id", "dates")
    assert second.inspect("a").confirmed["metric_id"] == "valid_order_count"
    assert second.inspect("b").confirmed is None


def test_checkpoint_has_only_bounded_business_state(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")])
    result = svc.run("a", "2024年订单数 PRIVATE_QUESTION_SENTINEL")
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        checkpoints = list(saver.list({"configurable": {"thread_id": "a"}}))
    assert checkpoints
    payload = json.dumps([item.checkpoint for item in checkpoints], ensure_ascii=False, default=str)
    assert "PRIVATE_QUESTION_SENTINEL" not in payload and result.request_id not in payload
    for forbidden in ("raw_response", "model_call_count", "analysis_result", "db_path", "candidate"):
        assert forbidden not in payload
    for item in checkpoints:
        assert set(item.checkpoint["channel_values"]) <= {
            "session", "__start__", "branch:to:begin", "branch:to:plan", "branch:to:merge",
            "branch:to:execute", "branch:to:finalize",
        }


def test_incomplete_turn_discards_work_and_recovers(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")])
    svc.run("a", "2024年订单数")
    state = svc.inspect("a")
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        ctx = TurnContext("never replay this", fixture_db, FakePlannerModel([]))
        build_graph(saver).invoke({"session": state.model_dump(exclude_none=True)},
                                 {"configurable": {"thread_id": "a"}}, context=ctx,
                                 interrupt_after=["begin"], durability="sync")
        assert ctx.model_call_count == 0
    restarted = service(fixture_db, tmp_path, [apply("refine", metric_id="effective_order_gmv_cents")])
    result = restarted.run("a", "改成成交额")
    assert result.status == "success" and result.recovered
    assert result.model_call_count == 1
    assert restarted.inspect("a").turn_count == 2


def test_same_thread_busy_other_thread_available(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")])
    with thread_lock(svc.checkpoint_db_path, "a"):
        with pytest.raises(ConversationError, match="thread_busy"):
            svc.run("a", "2024年订单数")
        assert svc.run("b", "2024年订单数").status == "success"
    assert svc.model.call_count == 1


@pytest.mark.parametrize("thread", ["", "../a", "x" * 129, "a/b", "a b", None])
def test_invalid_thread(fixture_db, tmp_path, thread):
    with pytest.raises(ConversationError, match="invalid_thread_id"):
        service(fixture_db, tmp_path).run(thread, "订单数")


def test_checkpoint_cannot_be_business_or_foreign_db(fixture_db, tmp_path):
    before = fixture_db.read_bytes()
    with pytest.raises(ConversationError, match="checkpoint_is_business_database"):
        ConversationService(fixture_db, fixture_db, model=FakePlannerModel([]))
    foreign = tmp_path / "foreign.db"
    foreign.write_bytes(before)
    with pytest.raises(ConversationError, match="foreign_checkpoint_database"):
        ConversationService(fixture_db, foreign, model=FakePlannerModel([]))
    assert fixture_db.read_bytes() == before and foreign.read_bytes() == before


def test_reference_is_stable_and_version_is_checked(fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")], reference_date="2024-06-30")
    svc.run("a", "今年订单数")
    assert service(fixture_db, tmp_path).inspect("a").reference_date == "2024-06-30"
    with pytest.raises(ConversationError, match="reference_date_mismatch"):
        service(fixture_db, tmp_path, reference_date="2024-12-31").inspect("a")
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        graph = build_graph(saver)
        raw = Session(reference_date="2024-06-30").model_dump(exclude_none=True)
        raw["state_version"] = "unknown"
        graph.update_state({"configurable": {"thread_id": "a"}}, {"session": raw})
    with pytest.raises(ConversationError, match="incompatible_checkpoint"):
        svc.run("a", "继续")


def test_concurrent_service_instances_hold_lock_for_entire_turn(fixture_db, tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingModel:
        model_name = "blocking-fake"

        def plan(self, question, context):
            entered.set()
            assert release.wait(15)
            return apply(metric_id="valid_order_count")

    first = ConversationService(fixture_db, tmp_path / "checkpoints.db", model=BlockingModel())
    second = service(fixture_db, tmp_path, [apply(metric_id="effective_order_gmv_cents")])
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(first.run, "a", "2024年订单数")
        try:
            assert entered.wait(15)
            with pytest.raises(ConversationError, match="thread_busy"):
                second.run("a", "2024年成交额")
            assert second.run("b", "2024年成交额").status == "success"
        finally:
            release.set()
        assert active.result(timeout=15).plan.metric_id == "valid_order_count"
    assert second.inspect("a").turn_count == 1
    assert second.inspect("b").confirmed["metric_id"] == "effective_order_gmv_cents"


def test_failed_turn_preserves_pending_across_restart(fixture_db, tmp_path):
    first = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count"), TimeoutError("MODEL_SECRET")])
    first.run("a", "订单数")
    before = first.inspect("a")
    assert first.run("a", "2024年").status == "model_error"
    restarted = service(fixture_db, tmp_path, [apply("clarify_reply")])
    assert restarted.inspect("a").pending == before.pending
    assert restarted.run("a", "2024年").status == "success"
    assert b"MODEL_SECRET" not in (tmp_path / "checkpoints.db").read_bytes()


@pytest.mark.parametrize("question,status", [("2024年订单数", "success"), ("订单数", "clarification_required")])
def test_explicit_new_topic_ignores_model_refine_and_persists(fixture_db, tmp_path, question, status):
    initial = service(fixture_db, tmp_path, [apply(metric_id="effective_order_gmv_cents", operation="breakdown",
        dimension_id="region", filters=[{"dimension_id": "region", "op": "eq", "value": "华东"}], top_n=2)])
    initial.run("a", "2024年成交额")

    class GuessRefine:
        model_name = "refine-fake"

        def plan(self, question, context):
            assert context["confirmed"] is None and context["pending"] is None
            return apply("refine", metric_id="valid_order_count")

    reset = ConversationService(fixture_db, tmp_path / "checkpoints.db", model=GuessRefine())
    result = reset.run("a", question, new_topic=True)
    assert result.status == status
    restarted = service(fixture_db, tmp_path)
    state = restarted.inspect("a")
    if status == "success":
        assert state.pending is None
        assert state.confirmed["metric_id"] == "valid_order_count"
        assert state.confirmed["operation"] == "total" and not state.confirmed["filters"]
        assert "top_n" not in state.confirmed
    else:
        assert state.confirmed is None and state.pending.intent == "new"
        assert state.pending.values.filters == () and state.pending.values.start_date is None
        resumed = service(fixture_db, tmp_path, [apply("clarify_reply")]).run("a", "2024年")
        assert resumed.status == "success" and resumed.plan.metric_id == "valid_order_count"


def test_new_topic_execution_failure_preserves_old_state(fixture_db, tmp_path, monkeypatch):
    from eda.query.errors import QueryError

    svc = service(fixture_db, tmp_path, [apply(metric_id="effective_order_gmv_cents"),
        {"status": "clarify", "intent": "refine", "missing": ["dimension_id"], "patch": {"set": {"operation": "breakdown"}}},
        apply("refine", metric_id="valid_order_count")])
    svc.run("a", "2024年成交额")
    svc.run("a", "按维度拆分")
    before = svc.inspect("a")

    def fail(*args, **kwargs):
        raise QueryError("db_error", "PRIVATE_EXECUTION_ERROR")

    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan", fail)
    result = svc.run("a", "2024年订单数", new_topic=True)
    assert result.status == "execution_error" and result.model_call_count == 1
    recovered = service(fixture_db, tmp_path).inspect("a")
    assert recovered.confirmed == before.confirmed and recovered.pending == before.pending


@pytest.mark.parametrize("node", ["plan", "merge", "execute"])
def test_intermediate_checkpoint_never_promotes_candidate(node, fixture_db, tmp_path):
    svc = service(fixture_db, tmp_path, [apply(metric_id="valid_order_count")])
    svc.run("a", "2024年订单数")
    before = svc.inspect("a")
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        ctx = TurnContext("2024年成交额 PRIVATE_REQUEST", fixture_db,
                          FakePlannerModel([apply(metric_id="effective_order_gmv_cents")]))
        graph = build_graph(saver)
        graph.invoke({"session": before.model_dump(exclude_none=True)}, {"configurable": {"thread_id": "a"}},
                     context=ctx, interrupt_after=[node], durability="sync")
        snapshot = graph.get_state({"configurable": {"thread_id": "a"}})
        assert Session.model_validate(snapshot.values["session"]).confirmed == before.confirmed
        assert "PRIVATE_REQUEST" not in json.dumps(snapshot.values)
        assert ctx.result is None
    resumed = service(fixture_db, tmp_path, [apply("refine")]).run("a", "继续")
    assert resumed.recovered and resumed.plan.metric_id == "valid_order_count"
