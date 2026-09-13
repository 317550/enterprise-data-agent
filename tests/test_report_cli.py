"""The manual cross-check CLI must stay read-only and honest about empty results."""

from __future__ import annotations

from pathlib import Path

import pytest

from eda.metrics.report import main


def test_report_prints_core_metrics(
    fixture_db: Path, capsys: pytest.CaptureFixture[str], expected_fixture_metrics
) -> None:
    full = next(
        s for s in expected_fixture_metrics["scenarios"] if s["name"] == "full_window"
    )
    exit_code = main(
        ["--db", str(fixture_db), "--start", "2024-01-01", "--end", "2024-12-31"]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert f"({full['revenue_cents']} 分)" in out
    assert f"{full['valid_order_count']} 单" in out


def test_report_reports_undefined_aov_instead_of_zero(
    fixture_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--db", str(fixture_db), "--start", "2023-01-01", "--end", "2023-12-31"]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "客单价无定义" in out


def test_report_fails_cleanly_when_the_database_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--db", str(tmp_path / "nope.db")])
    assert exit_code == 1
    assert "not found" in capsys.readouterr().out


def test_report_rejects_a_reversed_date_range(
    fixture_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--db", str(fixture_db), "--start", "2024-12-31", "--end", "2024-01-01"]
    )
    assert exit_code == 1
    assert "invalid filters" in capsys.readouterr().out


def test_report_breakdown_dimension_is_allow_listed(fixture_db: Path) -> None:
    """argparse rejects anything outside the allow-list before SQL is built."""
    with pytest.raises(SystemExit):
        main(["--db", str(fixture_db), "--by", "order_region; DROP TABLE orders --"])
