"""Offline presentation, audit and real Streamlit script tests."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tomllib
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from eda.conversation.fake import FakeConversationModel
from eda.conversation.service import ConversationService
from eda.viz.models import ChartSpec
from eda.viz.spec import validate_chart, figure
from eda.viz.transform import to_view
from eda.viz import runtime
from eda.audit.models import AuditRecord, make_record
from eda.audit.store import audit_path, write_record, AUDIT_WARNING

ROOT = Path(__file__).resolve().parents[1]
MONTHS = "对比2024年2月和2024年1月的成交额"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def svc(fixture_db, tmp_path):
    return ConversationService(fixture_db, tmp_path / "cp.db", model=FakeConversationModel(), reference_date="2024-12-31")


def spec(**changes):
    return dict(chart_type="bar", title="分类结果", x_field="category", y_field="value", x_kind="category", y_unit="分", sort_direction="none", source_kind="single", completeness=True) | changes


@pytest.mark.parametrize("change", [{"chart_type": "pie"}, {"sql": "SELECT 1"}, {"title": "SELECT * FROM orders"}, {"title": "lambda: 1"}, {"title": "https://evil.test"}, {"title": "<b>x</b>"}, {"x_field": "a+b"}, {"y_field": "orders"}, {"completeness": "true"}, {"callback": print}, {"color": "red"}, {"vega": {}}])
def test_chart_rejects(change):
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(spec(**change))


@pytest.mark.parametrize("raw", [ChartSpec.model_construct(**spec(sql="secret")), ChartSpec(**spec()).model_copy(update={"x_field": "secret"}), ChartSpec(**spec()).model_copy(update={"sql": "secret"})])
def test_chart_instance_bypass(raw):
    with pytest.raises(ValidationError):
        validate_chart(raw, [{"category": "华东", "value": 1}])


def test_missing_coordinates():
    with pytest.raises(ValueError):
        validate_chart(spec(), [{"category": "x"}])


@pytest.mark.parametrize("question,chart_type", [("2024年成交额", None), ("2024年按地区成交额", "bar"), (MONTHS, "bar"), ("查看2024年2月成交额环比", "line"), (MONTHS + "并按地区看贡献", "bar")])
def test_views(svc, question, chart_type):
    result = svc.run(uuid4().hex, question)
    assert result.status == "success"
    view = to_view(result, 12.0)
    assert view.unit == "分" and view.technical["elapsed_ms"] == 12.0
    assert (view.chart.chart_type if view.chart else None) == chart_type
    assert view.finding_type in {"observation", "decomposition"}
    assert view.evidence
    if view.chart:
        assert figure(view.chart, view.chart_rows).data
    if result.comparison:
        assert view.kpis["current_value"] == str(result.comparison.current)
        assert "sql" not in view.technical
    else:
        assert view.technical["sql"] == result.analysis_result.execution.sql


def test_mom_dates(svc):
    view = to_view(svc.run("a", "查看2024年1月成交额环比"))
    assert [r["period"] for r in view.chart_rows] == ["2023-12-01", "2024-01-01"]


def test_contributions_top_n_and_dimension(svc):
    result = svc.run("a", MONTHS + "并按地区看贡献前1")
    view = to_view(result)
    assert view.hidden["hidden_dimension_count"] == 2
    assert view.hidden["hidden_net_change"] == str(result.contribution.hidden_net_change)
    assert view.chart.source_kind == "contribution"
    full = to_view(svc.run("b", MONTHS + "并按地区看贡献"))
    assert any(float(r["change"]) < 0 for r in full.chart_rows)
    category = to_view(svc.run("b", "改按类别看贡献"))
    assert category.hidden["dimension_id"] == "category"
    assert category.rows != full.rows


def test_zero_and_empty(svc):
    view = to_view(svc.run("a", "对比2020年2月和2020年1月成交额并按地区看贡献"))
    assert view.kpis["change_rate"] is None
    assert "无定义" in view.kpis["change_rate_display"]
    assert not view.rows and view.chart is None


def test_ranking_retains_table_without_population_chart(svc):
    result = svc.run("rank", "2024年按地区成交额前1")
    view = to_view(result)
    assert view.status == "success" and view.operation == "排名"
    assert len(view.rows) == 1 and view.chart is None
    assert not view.completeness["is_complete_population"]


def test_zero_total_change_keeps_undefined_contribution(svc):
    from decimal import Decimal
    result = svc.run("zero", MONTHS + "并按地区看贡献")
    comparison = result.comparison.model_copy(update={"absolute_change": Decimal(0)})
    rows = tuple(row.model_copy(update={"contribution_rate": None}) for row in result.contribution.rows)
    contribution = result.contribution.model_copy(update={"rows": rows, "reconciled_change": Decimal(0)})
    view = to_view(result.model_copy(update={"comparison": comparison, "contribution": contribution}))
    assert all(row["contribution_rate"] is None and row["contribution_rate_display"] == "无定义" for row in view.rows)


def test_empty_breakdown_and_failed_result(svc):
    result = svc.run("empty", "2020年按地区成交额")
    assert not to_view(result).rows and to_view(result).chart is None
    error = result.model_copy(update={"status": "execution_error", "error_code": "incomplete_result"})
    assert to_view(error).chart is None


@pytest.mark.parametrize("changes", [{"truncated": True}, {"is_complete_population": False}])
def test_incomplete_no_chart(svc, changes):
    result = svc.run("a", "2024年按地区成交额")
    single = result.analysis_result
    single = single.model_copy(update={"completeness": single.completeness.model_copy(update=changes)})
    view = to_view(result.model_copy(update={"analysis_result": single}))
    assert view.chart is None


@pytest.mark.parametrize("status", ["clarification_required", "refused", "model_error", "plan_validation_error", "execution_error"])
def test_errors_redacted(svc, status):
    result = svc.run("a", "2024年成交额")
    result = result.model_copy(update={"status": status, "error_code": "PRIVATE_KEY_SQL", "refusal_reason": "PRIVATE_KEY_SQL", "clarification": "PRIVATE_KEY_SQL"})
    view = to_view(result)
    assert "PRIVATE" not in view.model_dump_json()
    assert not view.rows and not view.plan and not view.evidence
    assert "sql" not in view.technical


def test_audit_private_idempotent_and_schema(svc, tmp_path, fixture_db):
    result = svc.run("private-thread", MONTHS + "并按地区看贡献")
    view = to_view(result)
    record = make_record(view, "private-thread", uuid4().hex)
    path = tmp_path / "audit.jsonl"
    args = dict(business_path=fixture_db, checkpoint_path=svc.checkpoint_db_path)
    assert write_record(path, record, **args) is None
    assert write_record(path, record, **args) is None
    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    for banned in ["private-thread", "华东", "成交额", "SELECT", "prompt\"", "question", "dimension_value", "baseline_value", "filters", "api_key"]:
        assert banned not in text
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(record.model_copy(update={"question": "secret"}))


@pytest.mark.parametrize("kind", ["business", "checkpoint", "hardlink", "foreign"])
def test_audit_path_isolation(svc, tmp_path, fixture_db, kind):
    svc.run("a", "2024年成交额")
    path = fixture_db if kind == "business" else svc.checkpoint_db_path
    if kind == "hardlink":
        path = tmp_path / "alias.jsonl"
        os.link(fixture_db, path)
    if kind == "foreign":
        path = tmp_path / "foreign.jsonl"
        path.write_bytes(fixture_db.read_bytes())
    with pytest.raises((ValueError, UnicodeError)):
        audit_path(path, fixture_db, svc.checkpoint_db_path)


def test_audit_concurrent_bounded(svc, tmp_path, fixture_db):
    record = make_record(to_view(svc.run("a", "2024年成交额")), "a", uuid4().hex)
    path = tmp_path / "audit.jsonl"
    def write(_):
        return write_record(path, record, business_path=fixture_db, checkpoint_path=svc.checkpoint_db_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(write, range(8)))
    assert all(x in (None, AUDIT_WARNING) for x in outcomes)
    assert len(path.read_text().splitlines()) == 1
    assert write_record(fixture_db, record, business_path=fixture_db, checkpoint_path=svc.checkpoint_db_path) == AUDIT_WARNING


@pytest.fixture
def app(monkeypatch, fixture_db, tmp_path):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(runtime, "BUSINESS", fixture_db)
    monkeypatch.setattr(runtime, "CHECKPOINT", tmp_path / "ui-cp.db")
    monkeypatch.setattr(runtime, "AUDIT", tmp_path / "ui-audit.jsonl")
    at = AppTest.from_file(str(ROOT / "app/streamlit_app.py"), default_timeout=30).run()
    assert not at.exception
    return at


def button(app, label):
    return next(b for b in app.button if b.label == label)


def ask(app, question):
    next(w for w in app.text_input if w.label == "问题").set_value(question)
    button(app, "提交").click().run()
    assert not app.exception
    return app.session_state.last_response


def test_ui_zero_calls_and_reruns(app, monkeypatch):
    calls = []
    original = ConversationService.run
    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ConversationService, "run", counted)
    assert app.session_state.provider == "fake"
    next(w for w in app.text_input if w.label == "问题").set_value("2024年成交额").run()
    assert not calls and not runtime.AUDIT.exists()
    view = ask(app, MONTHS)
    assert view.status == "success" and len(calls) == 1
    assert len(app.get("plotly_chart")) == 1
    audit = runtime.AUDIT.read_bytes()
    app.run()
    assert len(calls) == 1 and runtime.AUDIT.read_bytes() == audit
    assert app.session_state.last_response == view


def test_ui_restore_new_and_contribution(app):
    ask(app, MONTHS)
    thread = app.session_state.thread_id
    button(app, "新建会话").click().run()
    assert app.session_state.thread_id != thread and app.session_state.last_response is None
    next(w for w in app.text_input if w.label == "已有 thread_id").set_value(thread)
    button(app, "恢复会话").click().run()
    assert app.session_state.thread_id == thread and app.session_state.last_response is None
    view = ask(app, "按地区看变化贡献")
    assert view.status == "success" and view.hidden["dimension_id"] == "region"
    assert len(app.get("plotly_chart")) == 1 and app.dataframe
    assert ask(app, "改按类别看贡献").hidden["dimension_id"] == "category"


def test_ui_real_missing_key_and_safe_errors(app, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app.selectbox[0].set_value("real").run()
    assert ask(app, "2024年成交额").status == "model_error"
    app.selectbox[0].set_value("fake").run()
    assert ask(app, "为什么成交额下降").status == "refused"
    assert ask(app, "成交额").status == "clarification_required"
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_KEY_DATABASE_PATH")
    monkeypatch.setattr(runtime, "service", broken)
    assert ask(app, "2024年成交额").status == "conversation_error"
    assert "PRIVATE" not in str([x.value for x in app.text])


def test_ui_fresh_browser_restore_and_new_topic(app):
    from streamlit.testing.v1 import AppTest
    ask(app, MONTHS)
    thread = app.session_state.thread_id
    fresh = AppTest.from_file(str(ROOT / "app/streamlit_app.py"), default_timeout=30).run()
    assert fresh.session_state.last_response is None
    next(w for w in fresh.text_input if w.label == "已有 thread_id").set_value(thread)
    button(fresh, "恢复会话").click().run()
    assert ask(fresh, "按地区看变化贡献").status == "success"
    fresh.checkbox[0].set_value(True)
    assert ask(fresh, "2024年订单数").plan["operation"] == "total"


def test_ui_processing_blocks_service(app, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "submit", lambda *a: calls.append(a))
    app.session_state.processing = True
    app.run()
    assert button(app, "提交").disabled
    assert not calls


def test_audit_failure_keeps_result(app, monkeypatch):
    monkeypatch.setattr(runtime, "write_record", lambda *a, **kw: AUDIT_WARNING)
    assert ask(app, "2024年成交额").status == "success"
    assert any(w.value == AUDIT_WARNING for w in app.warning)


@pytest.mark.parametrize("provider", ["real", "fake"])
def test_pre_service_failure_has_safe_trace(app, monkeypatch, provider):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_KEY_DATABASE_PATH")
    monkeypatch.setattr(runtime, "service", broken)
    run_id = uuid4().hex
    view, warning = runtime.submit("trace", "private question", provider,
                                   "2024-12-31", False, run_id)
    assert warning is None
    assert view.technical["request_id"] == run_id
    assert view.technical["model_call_count"] == view.technical["query_count"] == 0
    assert view.technical["node_path"] == ()
    assert all(view.technical[k] for k in ("prompt_version", "state_version", "semantic_version"))
    record = json.loads(runtime.AUDIT.read_text(encoding="utf-8"))
    assert record["request_id"] == run_id
    assert view.technical["elapsed_ms"] >= record["elapsed_ms"]
    assert "PRIVATE" not in view.model_dump_json()
    assert "private question" not in runtime.AUDIT.read_text(encoding="utf-8")


def test_packaging_and_dependencies(tmp_path):
    from importlib.metadata import version
    from packaging.requirements import Requirement
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert {"eda.viz", "eda.audit"} <= set(config["tool"]["setuptools"]["packages"])
    for dependency in config["project"]["dependencies"]:
        req = Requirement(dependency)
        if req.name in {"streamlit", "pandas", "plotly"}:
            assert version(req.name) in req.specifier
            for name in ("requirements.txt", "requirements.lock.txt"):
                assert f"{req.name}=={version(req.name)}" in (ROOT / name).read_text()
    # Build/install the actual wheel, then import outside the checkout without pytest pythonpath.
    wheel_dir = tmp_path / "wheels"
    child = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheel_dir), str(ROOT)], capture_output=True, timeout=90)
    assert child.returncode == 0, child.stderr
    target = tmp_path / "installed"
    child = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--target", str(target), str(next(wheel_dir.glob("*.whl")))], capture_output=True, timeout=90)
    assert child.returncode == 0, child.stderr
    env = {**os.environ, "PYTHONPATH": str(target)}
    child = subprocess.run([sys.executable, "-c", "import eda.viz,eda.audit; print(eda.viz.__file__); print(eda.audit.__file__)"], cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert child.returncode == 0 and str(target).encode() in child.stdout
