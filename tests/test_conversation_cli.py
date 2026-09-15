import json
import os
import subprocess
import sys

import pytest

from eda.conversation.cli import main
from eda.conversation.fake import FakeConversationModel
from eda.conversation.service import ConversationService


@pytest.mark.parametrize("flags,expected", [([], 30.0), (["--timeout-seconds", "60"], 60.0)])
def test_cli_timeout_reaches_shared_deadline(monkeypatch, fixture_db, tmp_path, capsys, flags, expected):
    from eda.conversation.graph import TurnContext

    original = TurnContext.__post_init__
    seen = []

    def inspect(ctx):
        ctx.clock = lambda: 100.0
        original(ctx)
        seen.append((ctx.timeout_seconds, ctx.deadline))

    monkeypatch.setattr(TurnContext, "__post_init__", inspect)
    assert main(["2024年订单数", "--thread", "timeout", "--db", str(fixture_db),
                 "--checkpoint-db", str(tmp_path / "cp.db"), *flags]) == 0
    assert seen == [(expected, 100.0 + expected)]
    assert json.loads(capsys.readouterr().out)["query_count"] == 1


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "-Infinity", "121", "0.5"])
def test_cli_invalid_timeout_before_service(monkeypatch, capsys, value):
    monkeypatch.setattr("eda.conversation.cli.ConversationService",
                        lambda *a, **kw: pytest.fail("invalid timeout must not create service"))
    assert main(["订单数", "--thread", "a", "--timeout-seconds=" + value]) == 7
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_input"


def test_cli_restarts_in_separate_processes(fixture_db, tmp_path):
    base = [sys.executable, "-m", "eda.conversation.cli", "--thread", "demo", "--db", str(fixture_db),
            "--checkpoint-db", str(tmp_path / "checkpoint.db")]
    for question, expected, flags in [("订单数", 2, []), ("2024年", 0, []), ("改成成交额", 0, []),
                                      ("客单价", 2, ["--new-topic"]), ("2024年", 0, [])]:
        completed = subprocess.run([*base, question, *flags], capture_output=True, text=True, encoding="utf-8",
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30)
        assert completed.returncode == expected, completed.stderr + completed.stdout
        payload = json.loads(completed.stdout)
        assert payload["status"] == ("success" if expected == 0 else "clarification_required")
        if expected == 0:
            assert payload["analysis_result"] and payload["model_call_count"] == 1


def test_offline_demo_refinement_and_clear(fixture_db, tmp_path):
    service = ConversationService(fixture_db, tmp_path / "cp.db", model=FakeConversationModel())
    initial = service.run("a", "2024年华东订单数按地区前2")
    assert initial.status == "success"
    result = service.run("a", "改成成交额")
    assert result.plan.top_n == 2 and result.plan.filters[0].value == "华东"
    result = service.run("a", "取消地区筛选")
    assert result.plan.filters == () and "filters.region" in result.cleared
    result = service.run("a", "取消拆分")
    assert result.plan.operation == "total" and result.plan.top_n is None


@pytest.mark.parametrize("args,code", [([], "invalid_input"), (["订单数", "--thread", "../x"], "invalid_thread_id"),
                                         (["订单数", "--thread", "a", "--provider", "invalid"], "invalid_input")])
def test_cli_safe_invalid_args(args, code, capsys, fixture_db, tmp_path):
    assert main([*args, "--db", str(fixture_db), "--checkpoint-db", str(tmp_path / "cp.db")]) == 7
    assert json.loads(capsys.readouterr().out)["error_code"] == code


def test_child_process_lock_release_after_termination(tmp_path):
    from eda.conversation.checkpoint import ConversationError, thread_lock

    path = tmp_path / "cp.db"
    script = """
import sys, time
from pathlib import Path
from eda.conversation.checkpoint import thread_lock
with thread_lock(Path(sys.argv[1]), 'a'):
    print('locked', flush=True)
    time.sleep(30)
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(ConversationError, match="thread_busy"):
            with thread_lock(path, "a"):
                pytest.fail("lock should be held by child")
        with thread_lock(path, "b"):
            pass
    finally:
        child.terminate()
        child.communicate(timeout=10)
    with thread_lock(path, "a"):
        pass


def test_service_recovers_after_process_dies_in_model_call(fixture_db, tmp_path):
    script = """
import sys, time
from eda.conversation.service import ConversationService
class Model:
    model_name = 'blocked-fake'
    def plan(self, question, context):
        print('started', flush=True)
        time.sleep(30)
ConversationService(sys.argv[1], sys.argv[2], model=Model()).run('a', '2024年')
"""
    path = tmp_path / "cp.db"
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(fixture_db), str(path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "started"
    finally:
        child.terminate()
        child.communicate(timeout=10)
    service = ConversationService(fixture_db, path, model=FakeConversationModel())
    assert service.inspect("a").turn_status == "in_progress"
    result = service.run("a", "2024年订单数")
    assert result.status == "success" and result.recovered and result.model_call_count == 1


def test_cli_new_topic_deterministic_reset_and_resume(fixture_db, tmp_path, capsys):
    base = ["--thread", "a", "--db", str(fixture_db), "--checkpoint-db", str(tmp_path / "cp.db")]
    assert main(["2024年华东成交额按地区前2", *base]) == 0
    capsys.readouterr()
    # Fake naturally guesses refine for this phrase; the CLI flag must win.
    assert main(["改成订单数", "--new-topic", *base]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["missing"] == ["dates"]
    state = ConversationService(fixture_db, tmp_path / "cp.db", model=FakeConversationModel()).inspect("a")
    assert state.confirmed is None and state.pending.intent == "new" and state.pending.values.filters == ()
    assert main(["2024年", *base]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plan"]["operation"] == "total" and not result["plan"]["filters"]
    assert result["plan"]["metric_id"] == "valid_order_count"
    assert main(["2024年客单价", "--new-topic", *base]) == 0
    assert json.loads(capsys.readouterr().out)["plan"]["metric_id"] == "aov_cents"


def test_cli_provider_selection_stays_offline(monkeypatch, fixture_db, tmp_path, capsys):
    from types import SimpleNamespace
    import socket

    def no_network(*args, **kwargs):
        pytest.fail("default CLI must not connect to the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)

    base = ["--thread", "a", "--db", str(fixture_db), "--checkpoint-db", str(tmp_path / "cp.db")]
    monkeypatch.setitem(sys.modules, "eda.conversation.real", SimpleNamespace(
        RealConversationModel=lambda: pytest.fail("default provider must remain fake")))
    assert main(["2024年订单数", *base]) == 0
    capsys.readouterr()
    model = FakeConversationModel()
    monkeypatch.setitem(sys.modules, "eda.conversation.real", SimpleNamespace(RealConversationModel=lambda: model))
    assert main(["2024年订单数", "--provider", "real", "--new-topic", *base]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success" and model.call_count == 1
