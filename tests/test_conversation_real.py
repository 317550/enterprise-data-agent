"""Real protocol/HTTPS contracts using mock transport only; network forbidden."""

import http.client
import json
import logging
import socket

import pytest

from eda.agent.models import MAX_OUTPUT_CHARS
from eda.agent.planner import ModelFailure
from eda.agent.real import RealPlannerModel
from eda.config import Settings
from eda.conversation.checkpoint import open_checkpointer
from eda.conversation.cli import main
from eda.conversation.context import planning_context
from eda.conversation.models import Session, TurnDecision
from eda.conversation.real import RealConversationModel
from eda.conversation.service import ConversationService


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("network forbidden in automated multi-turn tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", blocked)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "TEST_ONLY_KEY_SENTINEL")
    monkeypatch.setattr("eda.agent.real.get_settings", lambda: Settings(_env_file=None))


def turn():
    return {"status": "apply", "intent": "new", "patch": {"set": {"metric_id": "valid_order_count"}}}


def prompt():
    return planning_context(Session(reference_date="2024-12-31"))


def transport(monkeypatch, contents=(), *, status=200, wire=None, failure=None):
    replies = iter(contents)
    calls = []

    class Connection:
        def __init__(self, host, port, timeout):
            self.record = {"host": host, "port": port, "timeout": timeout, "closed": False}
            calls.append(self.record)

        def request(self, method, path, body, headers):
            self.record.update(method=method, path=path, body=json.loads(body), headers=headers)
            if failure:
                raise failure

        def getresponse(self):
            return self

        def read(self, limit):
            self.record["read_limit"] = limit
            if wire is not None:
                return wire[:limit]
            return json.dumps({"choices": [{"message": {"content": next(replies)}}]}).encode()

        def close(self):
            self.record["closed"] = True

    Connection.status = status
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    return calls


def test_real_turn_protocol_and_allowlisted_history(monkeypatch):
    calls = transport(monkeypatch, [json.dumps(turn())])
    context = prompt()
    context.update(messages=["RAW_HISTORY_SENTINEL"], sql="QUERY_SENTINEL", rows=["ROW_SENTINEL"],
                   rules=["OVERRIDE_SENTINEL"], decision_schema={"bad": "SCHEMA_SENTINEL"})
    result = RealConversationModel().plan("2024年订单数", context)
    assert isinstance(result, TurnDecision) and result.status == "apply"
    assert len(calls) == 1 and calls[0]["closed"]
    body = calls[0]["body"]
    assert body["stream"] is False and body["response_format"] == {"type": "json_object"}
    assert calls[0]["host"] == "api.deepseek.com" and calls[0]["path"] == "/chat/completions"
    assert calls[0]["read_limit"] == MAX_OUTPUT_CHARS * 8 + 1
    system = body["messages"][0]["content"]
    for forbidden in ("RAW_HISTORY_SENTINEL", "QUERY_SENTINEL", "ROW_SENTINEL", "OVERRIDE_SENTINEL", "SCHEMA_SENTINEL", "PlannerDecision"):
        assert forbidden not in system
    assert "TurnDecision" in system and len(body["messages"]) == 2


@pytest.mark.parametrize("content", [
    '{"status":"ready","plan":{}}', '{"sql":"SELECT 1"}',
    '{"status":"apply","intent":"new","patch":{"set":{"top_n":null}}}',
    '{"status":"refuse","status":"apply"}', "not JSON", "x" * (MAX_OUTPUT_CHARS + 1),
], ids=["single-turn", "sql", "null", "duplicate-key", "not-json", "too-long"])
def test_real_rejects_other_protocol_and_invalid_content(monkeypatch, content):
    calls = transport(monkeypatch, [content])
    assert RealConversationModel().plan("问题", prompt()) == ""
    assert len(calls) == 1


@pytest.mark.parametrize("wire", [b"x" * (MAX_OUTPUT_CHARS * 8 + 1), b"invalid json",
    json.dumps({"choices": [{"message": {"content": json.dumps(turn()), "tool_calls": [{"id": "x"}]}}]}).encode(),
    json.dumps({"choices": [{"message": {"content": json.dumps(turn()), "function_call": {"name": "x"}}}]}).encode(),
], ids=["oversized-wire", "bad-json", "tool-call", "function-call"])
def test_wire_limits_and_tool_calls(monkeypatch, wire):
    calls = transport(monkeypatch, wire=wire)
    assert RealConversationModel().plan("问题", prompt()) == ""
    assert len(calls) == 1 and calls[0]["closed"]


@pytest.mark.parametrize("url", ["http://api.deepseek.com", "https://user:password@api.deepseek.com", "https://api.deepseek.com?x=1", "https://api.deepseek.com#fragment", "https://",
                               "https://api.deepseek.com:PRIVATE_PORT", "https://[INVALID_IPV6", "https://@api.deepseek.com"])
def test_https_configuration_rejected_before_transport(monkeypatch, url):
    calls = transport(monkeypatch)
    model = RealConversationModel()
    model._base_url = url
    with pytest.raises(ModelFailure, match="model_configuration_error"):
        model.plan("问题", prompt())
    assert not calls


@pytest.mark.parametrize("phase", ["construct", "close"])
def test_transport_lifecycle_exceptions_are_redacted(monkeypatch, phase):
    calls = transport(monkeypatch, [json.dumps(turn())])
    connection = http.client.HTTPSConnection

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_LIFECYCLE_EXCEPTION")

    if phase == "construct":
        monkeypatch.setattr(http.client, "HTTPSConnection", fail)
    else:
        monkeypatch.setattr(connection, "close", fail)
    with pytest.raises(ModelFailure, match="^model_unavailable$"):
        RealConversationModel().plan("问题", prompt())
    assert len(calls) == (0 if phase == "construct" else 1)


def test_environment_key_only_and_cli_stable_error(monkeypatch, fixture_db, tmp_path, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setattr("eda.agent.real.get_settings", lambda: Settings(_env_file=None, deepseek_api_key="DOTENV_ONLY_SENTINEL"))
    calls = transport(monkeypatch)
    assert main(["2024年订单数", "--thread", "a", "--provider", "real", "--db", str(fixture_db),
                 "--checkpoint-db", str(tmp_path / "cp.db")]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error" and payload["error_code"] == "model_configuration_error"
    assert "DOTENV_ONLY_SENTINEL" not in json.dumps(payload) and not calls


@pytest.mark.parametrize("status,failure,code", [(429, None, "model_unavailable"), (500, None, "model_unavailable"),
    (200, TimeoutError("PRIVATE_ERROR_SENTINEL"), "model_timeout"),
    (200, OSError("PRIVATE_ERROR_SENTINEL"), "model_unavailable")])
def test_transport_errors_are_terminal_no_retries(monkeypatch, fixture_db, tmp_path, status, failure, code):
    calls = transport(monkeypatch, status=status, failure=failure)
    svc = ConversationService(fixture_db, tmp_path / "cp.db", model=RealConversationModel())
    result = svc.run("a", "2024年订单数")
    assert result.status == "model_error" and result.error_code == code
    assert result.model_call_count == len(calls) == 1 and calls[0]["closed"]
    assert "PRIVATE_ERROR_SENTINEL" not in result.model_dump_json()


def test_one_graph_repair_and_no_raw_material_in_logs_or_checkpoints(monkeypatch, fixture_db, tmp_path, caplog):
    calls = transport(monkeypatch, ['{"raw":"PRIVATE_OUTPUT_SENTINEL"}', json.dumps(turn())])
    svc = ConversationService(fixture_db, tmp_path / "cp.db", model=RealConversationModel())
    with caplog.at_level(logging.DEBUG):
        result = svc.run("a", "2024年订单数 PRIVATE_QUESTION_SENTINEL")
    assert result.status == "success" and result.model_call_count == len(calls) == 2
    assert '"repair_error": "invalid_plan"' in calls[1]["body"]["messages"][0]["content"]
    assert "PRIVATE_OUTPUT_SENTINEL" not in calls[1]["body"]["messages"][0]["content"]
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        saved = json.dumps([item._asdict() for item in saver.list({"configurable": {"thread_id": "a"}})], default=str)
    for token in ("PRIVATE_OUTPUT_SENTINEL", "PRIVATE_QUESTION_SENTINEL", "TEST_ONLY_KEY_SENTINEL", "decision_schema"):
        assert token not in saved + caplog.text


def test_shared_transport_keeps_single_turn_response(monkeypatch):
    raw = '{"status":"clarify","clarification":"请提供明确的起止日期，或一个明确的年份、月份。"}'
    calls = transport(monkeypatch, [raw])
    assert RealPlannerModel().plan("订单数", {"prompt_version": "nl-plan-v1"}) == raw
    assert len(calls) == 1


def test_real_receives_only_current_draft_and_filters_are_not_logged(monkeypatch, fixture_db, tmp_path, caplog):
    first = turn()
    first["patch"]["set"]["filters"] = [{"dimension_id": "region", "op": "eq", "value": "华东"}]
    calls = transport(monkeypatch, [json.dumps(first), '{"status":"apply","intent":"clarify_reply"}'])
    svc = ConversationService(fixture_db, tmp_path / "cp.db", model=RealConversationModel())
    with caplog.at_level(logging.DEBUG):
        assert svc.run("a", "订单数 PRIVATE_PREVIOUS_QUESTION").status == "clarification_required"
        result = svc.run("a", "2024年")
    assert result.status == "success" and result.plan.filters[0].value == "华东"
    sent = json.loads(calls[1]["body"]["messages"][0]["content"].split("\n", 1)[1])
    assert sent["confirmed"] is None
    assert sent["pending"]["values"]["filters"] == first["patch"]["set"]["filters"]
    assert "PRIVATE_PREVIOUS_QUESTION" not in json.dumps(calls[1]["body"])
    assert "华东" not in caplog.text and "PRIVATE_PREVIOUS_QUESTION" not in caplog.text
    # Only approved business filters persist, not prompt/response copies.
    assert svc.inspect("a").confirmed["filters"][0]["value"] == "华东"
    with open_checkpointer(svc.checkpoint_db_path) as saver:
        saved = json.dumps([item._asdict() for item in saver.list({"configurable": {"thread_id": "a"}})], default=str)
    for forbidden in ("PRIVATE_PREVIOUS_QUESTION", "TEST_ONLY_KEY_SENTINEL", "messages", "decision_schema", "raw_response"):
        assert forbidden not in saved


def test_real_comparative_mock_repair_and_restored_drill(monkeypatch, fixture_db, tmp_path):
    compare = {"status": "apply", "intent": "new", "patch": {"set": {
        "operation": "compare", "metric_id": "effective_order_gmv_cents"}}}
    drill = {"status": "apply", "intent": "refine", "patch": {"set": {
        "operation": "contribution", "dimension_id": "region"}}}
    calls = transport(monkeypatch, ['{"sql":"private"}', json.dumps(compare), json.dumps(drill)])
    path = tmp_path / "cp.db"
    first = ConversationService(fixture_db, path, model=RealConversationModel(), reference_date="2024-12-31")
    result = first.run("a", "对比2024年2月和2024年1月的成交额")
    assert result.status == "success" and result.model_call_count == 2 and result.query_count == 2
    second = ConversationService(fixture_db, path, model=RealConversationModel())
    result = second.run("a", "按地区看变化贡献")
    assert result.status == "success" and result.query_count == 4 and result.model_call_count == 1
    sent = json.loads(calls[2]["body"]["messages"][0]["content"].split("\n", 1)[1])
    assert sent["confirmed_type"] == "comparative"
    assert sent["confirmed"]["baseline_period"]["start_date"] == "2024-01-01"
    assert "remaining_seconds" not in sent and "comparison" not in sent["confirmed"]
    assert all(call["timeout"] <= 30 for call in calls)


def test_real_shared_transport_honors_remaining_timeout_and_request_bound(monkeypatch):
    calls = transport(monkeypatch, [json.dumps(turn())])
    model = RealConversationModel()
    context = prompt()
    context["remaining_seconds"] = 0.25
    assert model.plan("订单数", context).status == "apply"
    assert calls[0]["timeout"] == 0.25
    with pytest.raises(ModelFailure, match="model_configuration_error"):
        model._request_json("x" * (MAX_OUTPUT_CHARS * 17), {})
    assert "body" not in calls[-1] and calls[-1]["closed"]


@pytest.mark.parametrize("configured,remaining,expected", [(60, 57, 57), (15, 57, 15), (120, 0.25, 0.25)])
def test_real_configured_and_remaining_timeout_minimum(monkeypatch, configured, remaining, expected):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", str(configured))
    calls = transport(monkeypatch, [json.dumps(turn())])
    context = prompt()
    context["remaining_seconds"] = remaining
    assert RealConversationModel().plan("订单数", context).status == "apply"
    assert calls[0]["timeout"] == expected


def test_cli_real_timeout_terminal_with_extended_budget(monkeypatch, fixture_db, tmp_path, capsys, caplog):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "60")
    calls = transport(monkeypatch, failure=TimeoutError("PRIVATE_TIMEOUT_SENTINEL"))
    monkeypatch.setattr("eda.conversation.graph.run_analysis_plan",
                        lambda *a, **kw: pytest.fail("timeout must not execute SQL"))
    with caplog.at_level(logging.DEBUG):
        code = main(["2024年订单数 PRIVATE_QUESTION_SENTINEL", "--thread", "timeout", "--provider", "real",
                     "--db", str(fixture_db), "--checkpoint-db", str(tmp_path / "cp.db"),
                     "--timeout-seconds", "60"])
    output = capsys.readouterr().out
    result = json.loads(output)
    assert code == 4 and result["error_code"] == "model_timeout"
    assert result["node_path"] == ["begin", "plan", "finalize"]
    assert result["model_call_count"] == len(calls) == 1 and result["query_count"] == 0
    assert 30 < calls[0]["timeout"] <= 60 and calls[0]["closed"]
    for token in ("PRIVATE_TIMEOUT_SENTINEL", "PRIVATE_QUESTION_SENTINEL", "TEST_ONLY_KEY_SENTINEL", "decision_schema"):
        assert token not in output + caplog.text
