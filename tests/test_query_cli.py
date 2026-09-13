"""Thin AnalysisPlan CLI: read JSON, print structured result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda.query.cli import main

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOTAL_PLAN = PROJECT_ROOT / "examples" / "plans" / "fixture_gmv_total.json"
CATEGORY_PLAN = PROJECT_ROOT / "examples" / "plans" / "fixture_gmv_by_category.json"


def test_cli_total_matches_fixture_expectation(
    fixture_db: Path, capsys: pytest.CaptureFixture[str], expected_fixture_metrics
) -> None:
    full = next(
        scenario
        for scenario in expected_fixture_metrics["scenarios"]
        if scenario["name"] == "full_window"
    )
    exit_code = main(["--plan", str(TOTAL_PLAN), "--db", str(fixture_db)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["rows"][0]["metric_value"] == full["revenue_cents"]
    assert payload["completeness"]["is_complete_population"] is True


def test_cli_category_ranking_matches_fixture_expectation(
    fixture_db: Path, capsys: pytest.CaptureFixture[str], expected_fixture_metrics
) -> None:
    expected = sorted(
        expected_fixture_metrics["breakdowns"]["category"],
        key=lambda row: (-row["revenue_cents"], row["dimension_value"]),
    )
    exit_code = main(["--plan", str(CATEGORY_PLAN), "--db", str(fixture_db)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [row["dimension_value"] for row in payload["rows"]] == [
        row["dimension_value"] for row in expected
    ]
    assert [row["metric_value"] for row in payload["rows"]] == [
        row["revenue_cents"] for row in expected
    ]


def test_cli_rejects_an_invalid_plan(
    tmp_path: Path, fixture_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "bad.json"
    plan.write_text(json.dumps({"metric_id": "nope", "operation": "total"}), encoding="utf-8")
    exit_code = main(["--plan", str(plan), "--db", str(fixture_db)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["error_code"] == "invalid_plan"


def test_cli_missing_database_returns_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist.db"
    exit_code = main(["--plan", str(TOTAL_PLAN), "--db", str(missing)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error_code"] == "db_error"
    assert "does-not-exist.db" not in payload["error_message"]
    assert "Traceback" not in captured.err
