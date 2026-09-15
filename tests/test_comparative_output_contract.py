"""Exact, compatible output contracts; all execution is offline and temporary."""

from dataclasses import asdict
from decimal import Decimal
import json
import socket

import pytest

from eda.conversation.checkpoint import open_checkpointer
from eda.conversation.cli import main
from eda.conversation.fake import FakeConversationModel
from eda.conversation.service import ConversationService
from eda.metrics.comparative import calculate_comparison, calculate_contribution
from eda.plan.comparative import ComparativeAnalysisPlan, month_period
from eda.query.comparative import run_comparative_analysis
from eda.query.comparative_models import Comparison, ContributionDetail


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("network forbidden")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def plan(operation="compare"):
    return ComparativeAnalysisPlan(metric_id="effective_order_gmv_cents", operation=operation,
        baseline_period=month_period(2024, 1), current_period=month_period(2024, 2),
        **({"dimension_id": "region"} if operation == "contribution" else {}))


@pytest.mark.parametrize("current,baseline,rate,display", [
    ("110", "100", "0.100000000000", "10.00%"),
    ("90", "100", "-0.100000000000", "-10.00%"),
    ("1", "0", None, "无定义"),
    ("1.23445", "1", "0.234450000000", "23.45%"),
])
def test_comparison_exact_compatible_display(current, baseline, rate, display):
    output = Comparison(**asdict(calculate_comparison(Decimal(current), Decimal(baseline))))
    data = json.loads(output.model_dump_json())
    assert data["current_value"] == data["current"] == current
    assert data["baseline_value"] == data["baseline"] == baseline
    assert isinstance(data["absolute_change"], str)
    assert data["change_rate"] == rate and data["change_rate_display"] == display
    assert data["evidence_ids"] == ["baseline_total", "current_total"]
    assert Comparison.model_validate(output).model_dump() == output.model_dump()


@pytest.mark.parametrize("current,baseline,expected", [
    ({"A": "15", "B": "5"}, {"A": "10", "B": "0"}, {"A": ("0.500000000000", "50.00%"), "B": ("0.500000000000", "50.00%")}),
    ({"A": "20", "B": "0"}, {"A": "5", "B": "5"}, {"A": ("1.500000000000", "150.00%"), "B": ("-0.500000000000", "-50.00%")}),
    ({"A": "10", "B": "0"}, {"A": "5", "B": "5"}, {"A": (None, "无定义"), "B": (None, "无定义")}),
])
def test_contribution_display_and_legacy_fields(current, baseline, expected):
    current = {k: Decimal(v) for k, v in current.items()}
    baseline = {k: Decimal(v) for k, v in baseline.items()}
    calculated = calculate_contribution(plan("contribution"), current, baseline,
        current_total=sum(current.values()), baseline_total=sum(baseline.values()))
    assert calculated.status == "success"
    for row in calculated.rows:
        output = ContributionDetail(**asdict(row))
        data = json.loads(output.model_dump_json())
        assert (data["contribution_rate"], data["contribution_rate_display"]) == expected[row.dimension_value]
        assert data["baseline_value"] == data["baseline"] == str(row.baseline)
        assert data["current_value"] == data["current"] == str(row.current)
        assert data["absolute_change"] == data["dimension_change"] == str(row.dimension_change)
        assert data["evidence_ids"] == ["baseline_breakdown", "current_breakdown"]


def assert_fixture_contract(data):
    comparison = data["comparison"]
    assert comparison["baseline_value"] == comparison["baseline"] == "223200"
    assert comparison["current_value"] == comparison["current"] == "232000"
    assert comparison["absolute_change"] == "8800"
    assert comparison["change_rate"] == "0.039426523297"
    assert comparison["change_rate_display"] == "3.94%"
    assert comparison["baseline_period"] == data["baseline_period"]
    assert comparison["current_period"] == data["current_period"]
    for row in data["contribution"]["rows"]:
        assert row["baseline_value"] == row["baseline"]
        assert row["current_value"] == row["current"]
        assert row["absolute_change"] == row["dimension_change"]
        assert isinstance(row["contribution_rate"], str)
        assert row["contribution_rate_display"].endswith("%")
        assert row["evidence_ids"] == ["baseline_breakdown", "current_breakdown"]


def test_public_service_cli_share_output_without_checkpoint_results(fixture_db, tmp_path, capsys):
    public = run_comparative_analysis(plan("contribution"), fixture_db)
    assert public.error_code is None
    assert_fixture_contract(json.loads(public.model_dump_json()))
    checkpoint = tmp_path / "cp.db"
    service = ConversationService(fixture_db, checkpoint, model=FakeConversationModel())
    question = "对比2024年2月和2024年1月的成交额，并按地区看变化贡献。"
    result = service.run("a", question)
    assert result.status == "success"
    assert_fixture_contract(json.loads(result.model_dump_json()))
    assert result.comparison == public.comparison and result.contribution == public.contribution
    assert main([question, "--thread", "cli", "--db", str(fixture_db),
                 "--checkpoint-db", str(checkpoint)]) == 0
    assert_fixture_contract(json.loads(capsys.readouterr().out))
    with open_checkpointer(checkpoint) as saver:
        saved = json.dumps([item._asdict() for item in saver.list(None)], default=str)
    for field in ("baseline_value", "current_value", "absolute_change", "change_rate_display",
                  "contribution_rate_display", "dimension_change", "evidence_ids"):
        assert field not in saved


def test_failure_does_not_gain_output_fields(fixture_db, tmp_path):
    public = run_comparative_analysis({"sql": "PRIVATE_SQL"}, fixture_db)
    assert public.comparison is None and public.contribution is None
    service = ConversationService(fixture_db, tmp_path / "cp.db",
        model=FakeConversationModel([TimeoutError("PRIVATE_EXCEPTION")]))
    result = service.run("a", "对比2024年和2023年的成交额")
    assert result.status != "success" and result.query_count == 0
    assert result.comparison is None and result.contribution is None and result.plan is None
    for token in ("baseline_value", "change_rate_display", "PRIVATE_EXCEPTION", "PRIVATE_SQL"):
        assert token not in public.model_dump_json() + result.model_dump_json()


def test_display_and_compatibility_aliases_are_code_owned():
    output = Comparison(**asdict(calculate_comparison(Decimal(110), Decimal(100))),
        baseline_value=Decimal(999), current_value=Decimal(999), change_rate_display="WRONG")
    assert output.baseline_value == 100 and output.current_value == 110
    assert output.change_rate_display == "10.00%"
    copied = output.model_copy(update={"change_rate_display": "WRONG"})
    assert Comparison.model_validate(copied).change_rate_display == "10.00%"


def test_fake_cli_undefined_rate_keeps_null(fixture_db, tmp_path, capsys):
    assert main(["对比2024年和2023年的成交额", "--thread", "zero", "--db", str(fixture_db),
        "--checkpoint-db", str(tmp_path / "cp.db")]) == 0
    data = json.loads(capsys.readouterr().out)["comparison"]
    assert data["baseline_value"] == "0" and data["current_value"] == "455200"
    assert data["change_rate"] is None and data["change_rate_display"] == "无定义"
