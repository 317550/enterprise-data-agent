"""Safe executor: authorizer, resource limits, timeout and connection hygiene."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from eda.db import connect_readonly
from eda.query.errors import QueryError
from eda.sql.authorizer import install_query_authorizer
from eda.sql.executor import (
    ExecutionLimits,
    execute_on_connection,
    execute_readonly_query,
    require_ok,
)

_SAFE_TOTAL = (
    "SELECT COALESCE(SUM(line_amount_cents), 0) AS metric_value "
    "FROM v_revenue_lines "
    "WHERE order_date >= :start_date AND order_date <= :end_date"
)
_DATES = {"start_date": "2024-01-01", "end_date": "2024-12-31"}


def _limits(**overrides) -> ExecutionLimits:
    payload = {
        "max_sql_chars": 8000,
        "max_rows": 1000,
        "max_result_bytes": 256000,
        "timeout_seconds": 10.0,
        "max_value_bytes": 4096,
    }
    payload.update(overrides)
    return ExecutionLimits(**payload)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_approved_view_query_returns_fixture_gmv(fixture_db: Path) -> None:
    result = require_ok(execute_readonly_query(fixture_db, _SAFE_TOTAL, _DATES))
    assert result.status == "ok"
    assert result.rows[0][0] == 455200
    assert result.truncated is False


def test_injection_style_parameter_does_not_change_sql(fixture_db: Path) -> None:
    sql = "SELECT COUNT(*) AS c FROM orders WHERE order_region = :region"
    before = _sha256(fixture_db)
    result = require_ok(
        execute_readonly_query(
            fixture_db, sql, {"region": "'; DROP TABLE orders --"}, limits=_limits()
        )
    )
    assert result.rows[0][0] == 0
    assert _sha256(fixture_db) == before


def test_authorizer_denies_write_without_going_through_the_parser(fixture_db: Path) -> None:
    conn = connect_readonly(fixture_db)
    try:
        install_query_authorizer(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO orders VALUES ('x','C001','2024-01-01','paid','华东','web')")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT name FROM sqlite_master")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA table_info(orders)")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE 'evil.db' AS evil")
    finally:
        conn.close()


def test_empty_result_is_success(fixture_db: Path) -> None:
    result = require_ok(
        execute_readonly_query(
            fixture_db,
            "SELECT order_id FROM orders WHERE order_date = :d",
            {"d": "1999-01-01"},
        )
    )
    assert result.status == "ok"
    assert result.rows == ()


def test_row_limit_distinguishes_exact_fit_from_truncation(fixture_db: Path) -> None:
    sql = (
        "SELECT category AS dimension_value, COALESCE(SUM(line_amount_cents), 0) AS metric_value "
        "FROM v_revenue_lines GROUP BY category ORDER BY dimension_value ASC"
    )
    exact = require_ok(execute_readonly_query(fixture_db, sql, {}, limits=_limits(max_rows=4)))
    assert exact.row_count == 4
    assert exact.truncated is False
    clipped = require_ok(execute_readonly_query(fixture_db, sql, {}, limits=_limits(max_rows=2)))
    assert clipped.row_count == 2
    assert clipped.truncated is True
    assert clipped.truncation_reason == "max_rows"


def test_byte_limit_excludes_the_overflow_row(fixture_db: Path) -> None:
    sql = (
        "SELECT category AS dimension_value FROM v_revenue_lines "
        "GROUP BY category ORDER BY category ASC"
    )
    tiny = require_ok(
        execute_readonly_query(fixture_db, sql, {}, limits=_limits(max_result_bytes=20))
    )
    assert tiny.truncated is True
    assert tiny.truncation_reason == "max_bytes"
    assert tiny.row_count < 4


def test_timeout_interrupts_an_allowed_join(demo_db: Path) -> None:
    sql = (
        "SELECT COUNT(*) AS c FROM order_items AS a "
        "INNER JOIN order_items AS b ON a.quantity >= 1 "
        "INNER JOIN order_items AS c ON b.quantity >= 1"
    )
    result = execute_readonly_query(demo_db, sql, {}, limits=_limits(timeout_seconds=0.2))
    assert result.status == "error"
    assert result.error_code == "timeout"
    assert result.rows == ()
    follow = require_ok(
        execute_readonly_query(
            demo_db, "SELECT COUNT(*) AS c FROM orders", {}, limits=_limits()
        )
    )
    assert follow.status == "ok"
    assert follow.rows[0][0] > 0


class _TrackingConnection:
    """Delegates to a real connection and counts close() without touching sqlite3 internals."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._inner.close()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def test_connection_is_closed_after_success_and_failure(
    fixture_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked: list[_TrackingConnection] = []
    real_connect = connect_readonly

    def tracking_connect(db_path):
        wrapper = _TrackingConnection(real_connect(db_path))
        tracked.append(wrapper)
        return wrapper

    monkeypatch.setattr("eda.sql.executor.connect_readonly", tracking_connect)

    require_ok(execute_readonly_query(fixture_db, "SELECT COUNT(*) AS c FROM orders"))
    assert len(tracked) == 1
    assert tracked[0].close_calls == 1
    failed = execute_readonly_query(fixture_db, "SELECT * FROM orders")
    assert failed.status == "error"
    assert len(tracked) == 2
    assert tracked[1].close_calls == 1


def test_failed_query_does_not_leave_authorizer_behind(fixture_db: Path) -> None:
    conn = connect_readonly(fixture_db)
    try:
        ok = execute_on_connection(conn, "SELECT COUNT(*) AS c FROM orders")
        assert ok.status == "ok"
        failed = execute_on_connection(conn, "SELECT * FROM orders")
        assert failed.status == "error"
        row = conn.execute("PRAGMA table_info(orders)").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_require_ok_raises_classified_error(fixture_db: Path) -> None:
    result = execute_readonly_query(fixture_db, "SELECT * FROM orders")
    with pytest.raises(QueryError) as exc:
        require_ok(result)
    assert exc.value.code == "unsupported_sql"


def test_sql_length_limit(fixture_db: Path) -> None:
    result = execute_readonly_query(
        fixture_db, "SELECT COUNT(*) AS c FROM orders", {}, limits=_limits(max_sql_chars=10)
    )
    assert result.status == "error"
    assert result.error_code == "resource_limit"


def test_missing_database_is_structured_db_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.db"
    result = execute_readonly_query(missing, "SELECT COUNT(*) AS c FROM orders")
    assert result.status == "error"
    assert result.error_code == "db_error"
    assert result.error_message == "business database was not found or could not be opened"
    assert "does-not-exist.db" not in (result.error_message or "")
    assert str(missing) not in (result.error_message or "")


def test_missing_sql_parameters_are_invalid_params(fixture_db: Path) -> None:
    result = execute_readonly_query(fixture_db, _SAFE_TOTAL, {})
    assert result.status == "error"
    assert result.error_code == "invalid_params"
    assert result.error_message == "SQL parameters are missing or invalid"
    assert "binding" not in (result.error_message or "").lower()


def test_sqlite_value_byte_limit_is_resource_limit(fixture_db: Path) -> None:
    result = execute_readonly_query(
        fixture_db,
        "SELECT 'abcdefghijklmnop' AS x FROM orders",
        {},
        limits=_limits(max_value_bytes=4),
    )
    assert result.status == "error"
    assert result.error_code == "resource_limit"
    assert "too big" not in (result.error_message or "").lower()


class _InterruptCursor:
    def __init__(self, inner: sqlite3.Cursor) -> None:
        self._inner = inner
        self.description = inner.description

    def fetchone(self):
        raise sqlite3.OperationalError("interrupted")

    def close(self) -> None:
        self._inner.close()


class _InterruptConnection:
    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner

    def execute(self, sql, parameters=()):
        if sql == "PRAGMA database_list":
            return self._inner.execute(sql, parameters)
        return _InterruptCursor(self._inner.execute(sql, parameters))

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def test_fetchone_interrupt_is_timeout(fixture_db: Path) -> None:
    conn = connect_readonly(fixture_db)
    try:
        result = execute_on_connection(
            _InterruptConnection(conn), "SELECT COUNT(*) AS c FROM orders"
        )
        assert result.status == "error"
        assert result.error_code == "timeout"
        assert "interrupt" not in (result.error_message or "").lower()
        row = conn.execute("PRAGMA table_info(orders)").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_execution_limits_must_be_positive() -> None:
    from pydantic import ValidationError

    base = {
        "max_sql_chars": 1,
        "max_rows": 1,
        "max_result_bytes": 1,
        "timeout_seconds": 1.0,
        "max_value_bytes": 1,
    }
    for field in base:
        payload = dict(base)
        payload[field] = 0
        with pytest.raises(ValidationError):
            ExecutionLimits(**payload)


def test_authorizer_allows_main_and_denies_other_databases() -> None:
    from eda.sql.authorizer import _authorize

    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "order_id", "main", None)
        == sqlite3.SQLITE_OK
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "ORDERS", "ORDER_ID", "MAIN", None)
        == sqlite3.SQLITE_OK
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "", "main", None) == sqlite3.SQLITE_OK
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "*", "main", None) == sqlite3.SQLITE_OK
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "v_revenue_lines", "line_amount_cents", "main", None)
        == sqlite3.SQLITE_OK
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "order_id", "temp", None)
        == sqlite3.SQLITE_DENY
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "order_id", "evil", None)
        == sqlite3.SQLITE_DENY
    )
    assert (
        _authorize(sqlite3.SQLITE_READ, "orders", "", None, None) == sqlite3.SQLITE_DENY
    )


def test_authorizer_bypass_allows_count_star_and_views(fixture_db: Path) -> None:
    conn = connect_readonly(fixture_db)
    try:
        install_query_authorizer(conn)
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] >= 0
        assert (
            conn.execute(
                "SELECT COALESCE(SUM(line_amount_cents), 0) FROM v_revenue_lines"
            ).fetchone()[0]
            is not None
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT order_id FROM temp.sqlite_master")
    finally:
        conn.close()


def test_allowed_and_denied_queries_do_not_change_database_bytes(fixture_db: Path) -> None:
    project_root = Path(__file__).resolve().parent.parent
    watched = [
        path
        for path in (project_root / "data" / "fixture.db", project_root / "data" / "business.db")
        if path.is_file()
    ]
    project_before = {path: _sha256(path) for path in watched}
    before = _sha256(fixture_db)
    require_ok(execute_readonly_query(fixture_db, _SAFE_TOTAL, _DATES))
    execute_readonly_query(fixture_db, "SELECT * FROM orders")
    execute_readonly_query(fixture_db, "SELECT name FROM sqlite_master")
    execute_readonly_query(
        fixture_db,
        "SELECT COUNT(*) AS c FROM orders WHERE order_region = :region",
        {"region": "'; DROP TABLE orders --"},
    )
    assert _sha256(fixture_db) == before
    for path, digest in project_before.items():
        assert _sha256(path) == digest, path
