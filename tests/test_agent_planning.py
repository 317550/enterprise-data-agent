"""Offline planning contracts, actual stage-two execution, and bounded failures."""

import hashlib
import http.client
import json
import logging
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eda.agent.cli import main
from eda.agent.context import build_context
from eda.agent.dates import resolve_dates
from eda.agent.fake import FakePlannerModel
from eda.agent.models import (
    DATE_QUESTION, DIMENSION_QUESTION, METRIC_QUESTION,
    DecisionError, PlannerDecision, clarify, parse_decision, refuse,
)
from eda.agent.planner import ModelFailure
from eda.agent.service import run_question
from eda.config import Settings
from eda.metrics.definitions import GMV_METRIC_ID, METRIC_REGISTRY
from eda.plan.models import AnalysisPlan
from eda.query.errors import QueryError
from eda.sql.compiler import compile_plan
from eda.sql.executor import ExecutionLimits


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("automated planning tests must never open a network connection")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", blocked)


def ready(**changes):
    payload = dict(metric_id=GMV_METRIC_ID, operation="total", start_date="2024-01-01", end_date="2024-12-31")
    payload.update(changes)
    return {"status": "ready", "plan": payload}


def run(fixture_db, model, question="2024年成交额是多少？", **kwargs):
    return run_question(question, fixture_db, model=model, reference_date=kwargs.pop("reference_date", "2024-12-31"), **kwargs)


@pytest.mark.parametrize("question,metric,dimension,top_n", [
    ("2024年成交额是多少？", GMV_METRIC_ID, None, None),
    ("2024年营业额总量是多少？", GMV_METRIC_ID, None, None),
    ("2024年各地区有效订单成交额是多少？", GMV_METRIC_ID, "region", None),
    ("2024年各类别成交额前3名", GMV_METRIC_ID, "category", 3),
    ("2024年各商品类别成交额前2名", GMV_METRIC_ID, "category", 2),
    ("2024年按地区订单数是多少？", "valid_order_count", "region", None),
    ("2024年客单价是多少？", "aov_cents", None, None),
    ("2024年按月销量是多少？", "item_quantity", "month", None),
    ("2024年按天成交额是多少？", GMV_METRIC_ID, "date", None),
])
def test_fake_grammar_and_actual_query_pipeline(fixture_db, expected_fixture_metrics, question, metric, dimension, top_n):
    model = FakePlannerModel()
    result = run(fixture_db, model, question)
    assert result.status == "success"
    assert result.model_call_count == model.call_count == 1
    assert model.repair_errors == [None]
    assert result.plan.metric_id == metric
    assert result.plan.dimension_id == dimension
    assert result.plan.top_n == top_n
    assert result.analysis_result.execution.sql == compile_plan(result.plan).sql
    assert result.query_id == result.analysis_result.query_id
    assert result.semantic_version == "1.1.0"
    assert result.prompt_version == "nl-plan-v1"
    assert (result.start_date, result.end_date) == ("2024-01-01", "2024-12-31")
    if metric == GMV_METRIC_ID and dimension is None:
        full = next(x for x in expected_fixture_metrics["scenarios"] if x["name"] == "full_window")
        assert result.analysis_result.rows[0].metric_value == full["revenue_cents"]
    if metric == GMV_METRIC_ID and dimension in ("region", "category"):
        expected = sorted(expected_fixture_metrics["breakdowns"][dimension], key=lambda x: (-x["revenue_cents"], x["dimension_value"]))
        if top_n:
            expected = expected[:top_n]
        assert [(row.dimension_value, row.metric_value) for row in result.analysis_result.rows] == [(x["dimension_value"], x["revenue_cents"]) for x in expected]


@pytest.mark.parametrize("text,reference,start,end,warning", [
    ("今年成交额", "2024-02-20", "2024-01-01", "2024-02-20", True),
    ("今年成交额", "2024-12-31", "2024-01-01", "2024-12-31", False),
    ("上个月成交额", "2024-03-15", "2024-02-01", "2024-02-29", False),
    ("上个月成交额", "2024-01-01", "2023-12-01", "2023-12-31", False),
    ("本月成交额", "2024-02-20", "2024-02-01", "2024-02-20", True),
    ("去年成交额", "2024-12-31", "2023-01-01", "2023-12-31", False),
    ("2024年2月成交额", "2024-02-15", "2024-02-01", "2024-02-29", True),
    ("2024-01-05至2024-01-12成交额", "2024-12-31", "2024-01-05", "2024-01-12", False),
    ("2024年1月5日至2024年1月12日成交额", "2024-12-31", "2024-01-05", "2024-01-12", False),
])
def test_reference_date_windows(fixture_db, text, reference, start, end, warning):
    model = FakePlannerModel()
    result = run(fixture_db, model, text, reference_date=reference)
    assert result.status == "success"
    assert (result.start_date, result.end_date) == (start, end)
    assert result.model_call_count == model.call_count == 1
    assert bool(result.warnings) == warning


@pytest.mark.parametrize("question", [
    "2024-2-1成交额", "2024-02-30成交额", "2024年2月30日成交额", "20240101成交额",
    "2024/01/01成交额", "2024-W01-1成交额", "2024年1月5日至6日成交额",
    "2024年2月到2024年3月成交额", "最近成交额", "成交额是多少", "今年和去年成交额",
    "2024-02-02到2024-01-01成交额",
])
def test_invalid_or_ambiguous_time_never_reaches_model_or_db(monkeypatch, fixture_db, question):
    def forbidden(*args, **kwargs):
        pytest.fail("ambiguous dates must not execute SQL")
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", forbidden)
    model = FakePlannerModel([ready()])
    result = run(fixture_db, model, question)
    assert result.status == "clarification_required"
    assert result.query_id is None and result.plan is None
    assert result.model_call_count == model.call_count == 0


@pytest.mark.parametrize("question", [
    "2024年订单表现怎么样？", "2024年成交额和订单数是多少？", "2024年各地区各类别成交额",
    "2024年北京成交额是多少？", "2024年排除华东的成交额", "2024年web成交额是多少？",
])
def test_fake_ambiguity_never_executes(monkeypatch, fixture_db, question):
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", lambda *a, **k: pytest.fail("must clarify"))
    model = FakePlannerModel()
    result = run(fixture_db, model, question)
    assert result.status == "clarification_required"
    assert result.model_call_count == model.call_count == 1
    assert model.repair_errors == [None]


@pytest.mark.parametrize("question,category", [
    ("删除2024年的订单", "write_request"), ("UPDATE orders", "write_request"),
    ("2024年查询sqlite_master", "unauthorized_request"), ("2024年读取用户密码", "unauthorized_request"),
    ("预测2024年成交额", "unsupported_analysis"), ("为什么2024年成交额下降", "unsupported_analysis"),
    ("2024年执行Python计算成交额", "unsupported_analysis"), ("2024年同比成交额", "unsupported_analysis"),
])
def test_explicit_refusals_have_zero_calls_and_no_sql(monkeypatch, fixture_db, question, category):
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", lambda *a, **k: pytest.fail("must refuse"))
    model = FakePlannerModel([ready()])
    result = run(fixture_db, model, question)
    assert result.status == "refused" and result.refusal_category == category
    assert result.model_call_count == model.call_count == 0
    assert result.validation_errors == ()
    assert result.query_id is None and result.analysis_result is None


@pytest.mark.parametrize("payload", [
    {**ready(), "clarification": None}, {"status": "ready"}, {"status": "ready", "plan": None},
    {"status": "clarify", "clarification": DATE_QUESTION, "plan": ready()["plan"]},
    {"status": "clarify", "clarification": "private-marker"},
    {"status": "refuse", "refusal_category": "write_request", "refusal_reason": "private-marker"},
    {"status": "refuse", "refusal_category": None, "refusal_reason": None},
    {**ready(), "sql": "SELECT private-marker"},
    ready(sql="SELECT private-marker"), ready(table="orders"), ready(column="order_id"),
    ready(expression="x"), ready(join="x"), ready(where="x"), ready(order_by_sql="x"),
    ready(top_n=True), ready(top_n=101), ready(metric_id="private-marker"),
    ready(filters=[{"dimension_id": "region", "op": "eq", "value": "private-marker"}]),
])
def test_decision_protocol_is_closed(payload):
    with pytest.raises(DecisionError) as caught:
        parse_decision(payload)
    assert str(caught.value) == "invalid_structure"


@pytest.mark.parametrize("bad", ["not json private-marker", "{}", "[]", '{"status":"ready","status":"clarify"}',
                                  {**ready(), "sql": "private-marker"}, ready(top_n=0)])
def test_one_repair_then_success(fixture_db, bad):
    model = FakePlannerModel([bad, ready()])
    result = run(fixture_db, model)
    assert result.status == "success"
    assert result.model_call_count == model.call_count == 2
    assert model.repair_errors[0] is None
    assert model.repair_errors[1] in {"invalid_json", "invalid_structure"}
    assert "private-marker" not in json.dumps(model.repair_errors)
    assert len(result.validation_errors) == 1


def test_budget_stops_after_second_invalid_output(monkeypatch, fixture_db):
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", lambda *a, **k: pytest.fail("invalid plan cannot execute"))
    model = FakePlannerModel(["bad", ready(top_n=-1), ready()])
    result = run(fixture_db, model)
    assert result.status == "plan_validation_error"
    assert result.error_code == "plan_generation_failed"
    assert result.model_call_count == model.call_count == 2
    assert result.query_id is None


@pytest.mark.parametrize("reply", [clarify(METRIC_QUESTION), refuse("unsupported_analysis")])
def test_model_terminal_decision_is_not_retried(monkeypatch, fixture_db, reply):
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", lambda *a, **k: pytest.fail("terminal decision cannot execute"))
    model = FakePlannerModel([reply, ready()])
    result = run(fixture_db, model)
    assert result.status in {"clarification_required", "refused"}
    assert result.model_call_count == model.call_count == 1


def test_forged_model_instances_are_fully_revalidated(fixture_db):
    forged = PlannerDecision.model_construct(status="ready", plan=AnalysisPlan.model_construct(**ready(metric_id="no_such_metric")["plan"]))
    model = FakePlannerModel([forged, forged])
    result = run(fixture_db, model)
    assert result.status == "plan_validation_error"
    assert result.model_call_count == model.call_count == 2


def test_legal_dimension_incompatible_with_metric_hits_real_branch(fixture_db, monkeypatch):
    definition = METRIC_REGISTRY[GMV_METRIC_ID]
    monkeypatch.setitem(METRIC_REGISTRY, GMV_METRIC_ID, definition.model_copy(update={"allowed_dimensions": ("region",)}))
    bad = ready(operation="breakdown", dimension_id="category")
    model = FakePlannerModel([bad, bad])
    result = run(fixture_db, model, "2024年各类别成交额")
    assert result.error_code == "plan_generation_failed"
    assert result.model_call_count == model.call_count == 2


def test_model_may_not_change_deterministic_dates(fixture_db):
    model = FakePlannerModel([ready(start_date="2023-01-01"), ready()])
    result = run(fixture_db, model)
    assert result.status == "success"
    assert result.validation_errors == ("date_mismatch",)
    assert model.repair_errors == [None, "date_mismatch"]
    assert result.model_call_count == model.call_count == 2


@pytest.mark.parametrize("error,code", [(TimeoutError("private-marker"), "model_timeout"),
                                        (RuntimeError("private-marker"), "model_unavailable"),
                                        (ModelFailure("model_configuration_error"), "model_configuration_error")])
def test_model_errors_are_safe_and_terminal(fixture_db, error, code):
    model = FakePlannerModel([error, ready()])
    result = run(fixture_db, model)
    assert result.status == "model_error" and result.error_code == code
    assert result.model_call_count == model.call_count == 1
    assert "private-marker" not in result.model_dump_json()


@pytest.mark.parametrize("code", ["unauthorized", "unsupported_sql", "resource_limit", "timeout", "db_error"])
def test_execution_errors_never_reach_model_repair(fixture_db, monkeypatch, code):
    def fail(*args, **kwargs):
        raise QueryError(code, "private-marker C:/private/file.db")
    monkeypatch.setattr("eda.agent.service.run_analysis_plan", fail)
    model = FakePlannerModel([ready(), ready()])
    result = run(fixture_db, model)
    assert result.status == "execution_error" and result.error_code == code
    assert result.model_call_count == model.call_count == 1
    assert result.query_id is None
    assert "private-marker" not in result.model_dump_json()


def test_actual_executor_truncation_and_no_reaggregation(fixture_db):
    limits = ExecutionLimits(max_rows=1, max_sql_chars=8000, max_result_bytes=256000, max_value_bytes=4096, timeout_seconds=10)
    model = FakePlannerModel()
    result = run(fixture_db, model, "2024年各类别成交额", limits=limits)
    assert result.status == "success"
    assert result.analysis_result.execution.truncated
    assert not result.analysis_result.completeness.reaggregation_safe
    assert "不能据此汇总总体" in result.summary
    assert "完整总体" not in result.summary
    assert result.model_call_count == model.call_count == 1


def test_actual_stage_two_resource_failure_has_no_retry(fixture_db):
    limits = ExecutionLimits(max_rows=1, max_sql_chars=1, max_result_bytes=256000, max_value_bytes=4096, timeout_seconds=10)
    model = FakePlannerModel([ready(), ready()])
    result = run(fixture_db, model, limits=limits)
    assert result.status == "execution_error"
    assert result.error_code == "resource_limit"
    assert result.model_call_count == model.call_count == 1
    assert result.query_id is None


def test_timeout_during_repair_still_counts_towards_budget(fixture_db):
    model = FakePlannerModel(["invalid", TimeoutError("private-marker"), ready()])
    result = run(fixture_db, model)
    assert result.status == "model_error" and result.error_code == "model_timeout"
    assert result.model_call_count == model.call_count == 2
    assert "private-marker" not in result.model_dump_json()


def test_budget_resets_per_request_when_model_is_reused(fixture_db):
    model = FakePlannerModel(["invalid", ready(), ready()])
    first, second = run(fixture_db, model), run(fixture_db, model)
    assert first.status == second.status == "success"
    assert first.model_call_count == 2 and second.model_call_count == 1
    assert model.call_count == 3
    assert first.request_id != second.request_id


def test_top_n_is_not_called_complete(fixture_db):
    model = FakePlannerModel()
    result = run(fixture_db, model, "2024年各类别成交额前2名")
    assert result.status == "success"
    assert not result.analysis_result.execution.truncated
    assert not result.analysis_result.completeness.is_complete_population
    assert "不能据此汇总总体" in result.summary
    assert result.model_call_count == model.call_count == 1


def test_actual_empty_result_and_zero_denominator(fixture_db):
    empty_model, ratio_model = FakePlannerModel(), FakePlannerModel()
    empty = run(fixture_db, empty_model, "2023年各类别成交额")
    ratio = run(fixture_db, ratio_model, "2023年客单价")
    assert empty.status == ratio.status == "success"
    assert empty.analysis_result.rows == () and "没有" in empty.summary
    assert ratio.analysis_result.rows[0].metric_value is None
    assert any("无定义" in warning for warning in ratio.warnings)
    assert empty.model_call_count == empty_model.call_count == ratio.model_call_count == ratio_model.call_count == 1


def test_context_has_no_storage_identifiers_or_database_discovery():
    from eda.metrics.definitions import SEMANTIC
    context = build_context("2024-12-31")
    assert "decision_schema" in context
    assert len(context["decision_schema"]["oneOf"]) == 3
    encoded = json.dumps(context, ensure_ascii=False)
    for forbidden in ("v_revenue_lines", "order_id", "order_region", "sqlite_master", "PRAGMA", "business.db"):
        assert forbidden not in encoded
    for metric in context["metrics"]:
        assert metric["id"] in SEMANTIC.metric_ids
        assert set(metric["operations"]) <= {"total", "breakdown"}


def test_audit_logs_never_include_question_plan_or_values(fixture_db, caplog):
    model = FakePlannerModel(["private-marker", ready()])
    with caplog.at_level(logging.INFO, logger="eda.agent.service"):
        result = run(fixture_db, model, "2024年private-marker成交额")
    assert result.status == "success"
    assert "private-marker" not in caplog.text
    log = json.loads(caplog.records[-1].getMessage().removeprefix("planning_request "))
    assert log["model_call_count"] == 2
    assert log["query_id"] == result.query_id
    assert set(log) == set(result.audit_record().model_dump(exclude_none=True))
    assert not {"question", "plan", "filters", "prompt", "raw_output"} & set(log)


def test_success_keeps_validated_plan_but_audit_redacts_filters(fixture_db, caplog):
    model = FakePlannerModel()
    with caplog.at_level(logging.INFO, logger="eda.agent.service"):
        result = run(fixture_db, model, "2024年华东成交额")
    assert result.status == "success"
    assert result.plan.filters[0].value == "华东"
    assert "华东" not in result.audit_record().model_dump_json()
    assert "华东" not in caplog.text
    assert result.model_call_count == model.call_count == 1


@pytest.mark.parametrize("question,exit_code,status,calls", [
    ("2024年成交额", 0, "success", 1), ("成交额", 2, "clarification_required", 0),
    ("删除订单", 3, "refused", 0), ("2024年客单价", 0, "success", 1),
])
def test_cli_defaults_to_fake(fixture_db, monkeypatch, capsys, question, exit_code, status, calls):
    import sys
    model = FakePlannerModel()
    monkeypatch.setattr("eda.agent.cli.FakePlannerModel", lambda: model)
    monkeypatch.setitem(sys.modules, "eda.agent.real", SimpleNamespace(RealPlannerModel=lambda: pytest.fail("real provider must be explicit")))
    assert main([question, "--db", str(fixture_db), "--reference-date", "2024-12-31"]) == exit_code
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    assert output["model_call_count"] == model.call_count == calls
    if status != "success":
        assert "query_id" not in output and "analysis_result" not in output


def test_explicit_real_provider_can_be_replaced_by_fake_without_network(fixture_db, monkeypatch, capsys):
    import sys
    model = FakePlannerModel()
    monkeypatch.setitem(sys.modules, "eda.agent.real", SimpleNamespace(RealPlannerModel=lambda: model))
    assert main(["2024年成交额", "--db", str(fixture_db), "--provider", "real"]) == 0
    assert json.loads(capsys.readouterr().out)["model_call_count"] == model.call_count == 1


@pytest.mark.parametrize("mode", ["model", "path", "arguments", "reference", "output"])
def test_cli_errors_redact_sensitive_text(fixture_db, tmp_path, monkeypatch, capsys, mode):
    marker = "private-marker-api-key-filter"
    model = FakePlannerModel([RuntimeError(marker)] if mode == "model" else [marker, marker]) if mode in {"model", "output"} else FakePlannerModel()
    monkeypatch.setattr("eda.agent.cli.FakePlannerModel", lambda: model)
    args = ["2024年成交额", "--db", str(tmp_path / marker) if mode == "path" else str(fixture_db)]
    if mode == "arguments":
        args.extend(["--provider", marker])
    if mode == "reference":
        args.extend(["--reference-date", marker])
    assert main(args) in {4, 5, 6}
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] in {"model_error", "plan_validation_error", "execution_error"}
    assert marker not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert model.call_count == {"model": 1, "output": 2, "path": 1, "arguments": 0, "reference": 0}[mode]


def test_cli_zero_denominator_keeps_json_null(fixture_db, capsys):
    assert main(["2023年客单价", "--db", str(fixture_db)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["analysis_result"]["rows"][0]["metric_value"] is None
    assert result["model_call_count"] == 1


def test_reference_date_is_explicit_config():
    assert Settings(_env_file=None, analysis_reference_date="2024-02-29").analysis_reference_date == "2024-02-29"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_reference_date="20240229")


def test_protected_assets_unchanged_after_success_and_failure(fixture_db):
    root = Path(__file__).resolve().parent.parent
    files = [*sorted((root / "eda/data/fixtures").glob("*.csv")), root / "tests/data/expected_fixture_metrics.json",
             *sorted((root / "data").glob("*.db"))]
    before = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in files}
    for question in ("2024年成交额", "删除订单", "成交额", "2024年各类别成交额前2名"):
        model = FakePlannerModel()
        result = run(fixture_db, model, question)
        assert result.model_call_count == model.call_count
    assert before == {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in files}
