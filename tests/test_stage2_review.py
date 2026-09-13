"""Independent regressions for the stage-two security review; no live services."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eda.db import connect_readonly
from eda.plan.models import AnalysisPlan, FilterClause, parse_analysis_plan
from eda.query.cli import main
from eda.query.errors import QueryError
from eda.query.service import run_analysis_plan
from eda.sql.authorizer import _authorize, install_query_authorizer
from eda.sql.compiler import compile_plan
from eda.sql.executor import ExecutionLimits, execute_on_connection, execute_readonly_query
from eda.sql.validator import validate_sql


def plan(**changes):
    return dict(metric_id="effective_order_gmv_cents", operation="breakdown",
                start_date="2024-01-01", end_date="2024-12-31",
                dimension_id="category", **changes)


def limits(**changes):
    values = dict(max_sql_chars=8000, max_rows=1000, max_result_bytes=256000,
                  timeout_seconds=10, max_value_bytes=4096)
    values.update(changes)
    return ExecutionLimits(**values)


@pytest.mark.parametrize("date", ["20240101", "2024-W01-1", "2024-02-30", "2024-1-1"])
def test_noncanonical_dates_rejected(date):
    payload = plan()
    payload["start_date"] = date
    with pytest.raises(QueryError, match="invalid_plan"):
        parse_analysis_plan(payload)


@pytest.mark.parametrize("top_n", [True, 1.0, "1", -1, 101])
def test_top_n_is_strict_integer(top_n):
    with pytest.raises(QueryError, match="invalid_plan"):
        parse_analysis_plan(plan(top_n=top_n))


@pytest.mark.parametrize("op,value", [("eq", "x" * 129), ("in", ["x" * 129])])
def test_filter_value_length_is_bounded(op, value):
    with pytest.raises(ValidationError):
        FilterClause(dimension_id="region", op=op, value=value)


@pytest.mark.parametrize("changes", [
    {"dimension_id": "orders; DROP TABLE orders"},
    {"operation": "compare"}, {"top_n": -1},
    {"filters": (FilterClause.model_construct(dimension_id="region", op="raw", value="x"),)},
])
def test_constructed_or_copied_plan_cannot_bypass_compiler(changes):
    for forged in (AnalysisPlan.model_construct(**{**plan(), **changes}),
                   AnalysisPlan.model_validate(plan()).model_copy(update=changes)):
        with pytest.raises(QueryError, match="invalid_plan"):
            compile_plan(forged)


@pytest.mark.parametrize("sql", [
    "SELECT o.order_id FROM orders o NATURAL JOIN customers c",
    "SELECT o.order_id FROM orders o CROSS JOIN customers c",
    "SELECT o.order_id FROM orders o RIGHT JOIN customers c ON o.customer_id=c.customer_id",
    "SELECT o.order_id FROM orders o FULL JOIN customers c ON o.customer_id=c.customer_id",
    "SELECT order_id FROM (SELECT order_id FROM orders) t",
    "WITH t AS (SELECT order_id FROM orders), T AS (SELECT order_id FROM orders) SELECT order_id FROM t",
    "WITH t(x) AS (SELECT order_id FROM orders) SELECT order_id FROM t",
    "WITH t AS (WITH RECURSIVE u AS (SELECT order_id FROM orders) SELECT order_id FROM u) SELECT order_id FROM t",
    "SELECT aux.orders.order_id FROM orders",
    "SELECT order_id FROM orderſ",
])
def test_uncertain_or_unsupported_ast_is_rejected(sql):
    with pytest.raises(QueryError):
        validate_sql(sql)


def test_cte_alias_column_case_matches_sqlite(fixture_db):
    sql = "WITH T AS (SELECT O.ORDER_ID AS X FROM ORDERS O) SELECT t.x FROM t ORDER BY X"
    result = execute_readonly_query(fixture_db, sql)
    assert result.status == "ok"
    assert result.row_count == 8


@pytest.mark.parametrize("sql", ["SELECT 'private-marker", 'SELECT "private-marker" FROM orders'])
def test_validator_errors_hide_parser_and_identifier_details(sql):
    with pytest.raises(QueryError) as caught:
        validate_sql(sql)
    assert "private-marker" not in str(caught.value)
    assert "sqlglot" not in str(caught.value).lower()


def test_deep_sql_is_safely_rejected_and_connection_recovers(fixture_db):
    sql = "SELECT " + "(" * 300 + "1" + ")" * 300
    with pytest.raises(QueryError, match="unsupported_sql"):
        validate_sql(sql)
    conn = connect_readonly(fixture_db)
    try:
        assert execute_on_connection(conn, sql).error_code == "unsupported_sql"
        assert execute_on_connection(conn, "SELECT COUNT(*) FROM orders").status == "ok"
    finally:
        conn.close()


def test_legacy_filters_also_reject_noncanonical_dates():
    from eda.metrics import MetricFilters
    with pytest.raises(ValidationError):
        MetricFilters(start_date="20240101", end_date="2024-12-31")


class ObservedConnection:
    def __init__(self, inner):
        self.inner = inner
        self.reads = []
        self.closed = 0
        self.authorizer_cleared = False
        self.progress_cleared = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def set_authorizer(self, callback):
        self.authorizer_cleared = callback is None
        if callback is None:
            self.inner.set_authorizer(None)
            return

        def observe(*args):
            decision = callback(*args)
            if args[0] == sqlite3.SQLITE_READ:
                self.reads.append((args, decision))
            return decision

        self.inner.set_authorizer(observe)

    def set_progress_handler(self, callback, steps):
        self.progress_cleared = callback is None
        self.inner.set_progress_handler(callback, steps)

    def close(self):
        self.closed += 1
        self.inner.close()


def test_real_count_star_callback_and_view_expansion(fixture_db):
    conn = ObservedConnection(connect_readonly(fixture_db))
    try:
        install_query_authorizer(conn)
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 8
        count_reads = conn.reads[:]
        # This records the real callback rather than manually assuming dbname.
        assert any(args[1:4] == ("orders", "", None) for args, _ in count_reads)
        conn.execute("SELECT order_id FROM orders").fetchall()
        assert any(args[1:4] == ("orders", "order_id", "main") for args, _ in conn.reads)
        conn.execute("SELECT SUM(line_amount_cents) FROM v_revenue_lines").fetchall()
        assert {args[1] for args, _ in conn.reads} >= {"orders", "order_items", "products", "v_revenue_lines"}
        assert all(decision == sqlite3.SQLITE_OK for _, decision in conn.reads)
    finally:
        conn.close()


@pytest.mark.parametrize("database", ["temp", "aux"])
@pytest.mark.parametrize("projection", ["order_id", "COUNT(*)"])
def test_real_foreign_database_read_is_denied(empty_writable_db, database, projection):
    raw = empty_writable_db
    if database == "aux":
        raw.execute("ATTACH DATABASE ':memory:' AS aux")
    raw.execute(f"CREATE TABLE {database}.orders (order_id TEXT)")
    raw.execute(f"INSERT INTO {database}.orders VALUES ('private-marker')")
    conn = ObservedConnection(raw)
    install_query_authorizer(conn)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"SELECT {projection} FROM {database}.orders").fetchall()
        assert any(args[1] == "orders" and decision == sqlite3.SQLITE_DENY
                   for args, decision in conn.reads)
        # Qualified COUNT scans retain the database name on SQLite 3.45.3.
        if projection == "COUNT(*)":
            assert any(args[2:4] == ("", database) for args, _ in conn.reads)
        assert conn.execute("SELECT order_id FROM main.orders").fetchall() == []
    finally:
        conn.set_authorizer(None)


@pytest.mark.parametrize("database", ["temp", "aux"])
def test_unqualified_foreign_count_has_no_database_and_is_denied(database):
    from eda.db import connect_for_build

    conn = ObservedConnection(connect_for_build(":memory:"))
    try:
        if database == "aux":
            conn.execute("ATTACH DATABASE ':memory:' AS aux")
        conn.execute(f"CREATE TABLE {database}.orders(order_id TEXT)")
        conn.execute(f"INSERT INTO {database}.orders VALUES ('private-marker')")
        install_query_authorizer(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT COUNT(*) FROM orders").fetchall()
        assert any(args[1:4] == ("orders", "", None) and decision == sqlite3.SQLITE_DENY
                   for args, decision in conn.reads)
    finally:
        conn.close()


def test_unknown_database_callback_defaults_to_deny():
    assert _authorize(sqlite3.SQLITE_READ, "orders", "order_id", None, None) == sqlite3.SQLITE_DENY
    assert _authorize(sqlite3.SQLITE_READ, "orders", "", None, None) == sqlite3.SQLITE_DENY


def test_executor_classifies_real_temp_shadow_denial(empty_writable_db):
    conn = ObservedConnection(empty_writable_db)
    conn.execute("CREATE TEMP TABLE orders(order_id TEXT)")
    sql = "SELECT order_id FROM orders"
    validate_sql(sql)
    result = execute_on_connection(conn, sql)
    assert result.error_code == "unauthorized"
    assert result.error_message == "SQLite authorizer denied this statement"
    assert any(args[3] == "temp" and decision == sqlite3.SQLITE_DENY for args, decision in conn.reads)
    assert conn.authorizer_cleared and conn.progress_cleared


@pytest.mark.parametrize("sql", ["SELECT upper(order_id) FROM orders", "PRAGMA user_version",
                                 "DELETE FROM orders", "ATTACH DATABASE ':memory:' AS aux"])
def test_authorizer_without_ast_denies_unapproved_actions(fixture_db, sql):
    conn = ObservedConnection(connect_readonly(fixture_db))
    try:
        install_query_authorizer(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("params", [{}, {"x": ["private-marker"]}, (), (1, 2), {"x": 2**100}])
def test_invalid_bindings_and_recovery(fixture_db, params):
    conn = ObservedConnection(connect_readonly(fixture_db))
    try:
        result = execute_on_connection(conn, "SELECT order_id FROM orders WHERE order_id=:x", params)
        assert result.error_code == "invalid_params"
        assert result.error_message == "SQL parameters are missing or invalid"
        assert "private-marker" not in result.model_dump_json()
        assert conn.authorizer_cleared and conn.progress_cleared
        assert execute_on_connection(conn, "SELECT COUNT(*) FROM orders").status == "ok"
    finally:
        conn.close()


@pytest.mark.parametrize("phase", ["execute", "fetchone"])
def test_real_sqlite_progress_interrupt_in_both_phases(fixture_db, monkeypatch, phase):
    from eda.sql import executor

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(executor, "time", SimpleNamespace(monotonic=lambda: clock.now))
    observations = []
    state = SimpleNamespace(phase=None, cursor_closed=False, interrupt=True)

    class Cursor:
        def __init__(self, inner):
            self.inner = inner
            self.description = inner.description

        def fetchone(self):
            state.phase = "fetchone"
            try:
                return self.inner.fetchone()
            except sqlite3.Error as exc:
                observations.append((state.phase, exc.sqlite_errorcode))
                raise

        def close(self):
            state.cursor_closed = True
            self.inner.close()

    class Connection(ObservedConnection):
        def execute(self, sql, params=()):
            if sql == "PRAGMA database_list":
                return self.inner.execute(sql, params)
            state.phase = "execute"
            try:
                return Cursor(self.inner.execute(sql, params))
            except sqlite3.Error as exc:
                observations.append((state.phase, exc.sqlite_errorcode))
                raise

        def set_progress_handler(self, callback, steps):
            self.progress_cleared = callback is None
            if callback is None:
                self.inner.set_progress_handler(None, 0)
                return

            def progress():
                if state.interrupt and state.phase == phase:
                    clock.now = 20.0
                return callback()

            self.inner.set_progress_handler(progress, 1)

    conn = Connection(connect_readonly(fixture_db))
    sql = "SELECT a.order_id FROM orders a JOIN orders b ON a.order_id=b.order_id"
    try:
        result = execute_on_connection(conn, sql, limits=limits())
        assert result.error_code == "timeout"
        assert observations == [(phase, sqlite3.SQLITE_INTERRUPT)]
        assert conn.authorizer_cleared and conn.progress_cleared
        if phase == "fetchone":
            assert state.cursor_closed
        state.interrupt = False
        clock.now = 0
        assert execute_on_connection(conn, "SELECT COUNT(*) FROM orders").status == "ok"
    finally:
        conn.close()


def test_corrupt_database_is_safe_error(tmp_path):
    path = tmp_path / "private-marker.db"
    path.write_bytes(b"not a SQLite database" * 100)
    result = execute_readonly_query(path, "SELECT COUNT(*) FROM orders")
    assert result.error_code == "db_error"
    assert str(path) not in result.model_dump_json()
    assert "private-marker" not in result.error_message


@pytest.mark.parametrize("failure", [False, True])
def test_engine_paths_close_and_restore_limits(fixture_db, monkeypatch, failure):
    conn = ObservedConnection(connect_readonly(fixture_db))
    previous = conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    monkeypatch.setattr("eda.sql.executor.connect_readonly", lambda _: conn)
    result = execute_readonly_query(fixture_db, "SELECT :x FROM orders",
                                   {} if failure else {"x": "ok"}, limits=limits(max_value_bytes=2000))
    assert result.status == ("error" if failure else "ok")
    assert conn.closed == 1
    assert conn.authorizer_cleared and conn.progress_cleared
    with pytest.raises(sqlite3.ProgrammingError):
        conn.inner.execute("SELECT 1")
    assert previous > 2000


def test_readonly_factory_closes_if_configuration_fails(fixture_db, monkeypatch):
    from eda import db
    conn = ObservedConnection(connect_readonly(fixture_db))
    monkeypatch.setattr(db.sqlite3, "connect", lambda *args, **kwargs: conn)
    def fail(_):
        raise sqlite3.DatabaseError("private-marker")
    monkeypatch.setattr(db, "_configure", fail)
    result = execute_readonly_query(fixture_db, "SELECT COUNT(*) FROM orders")
    assert result.error_code == "db_error"
    assert conn.closed == 1
    assert "private-marker" not in result.error_message


def test_utf8_byte_budget_and_limit_restoration(fixture_db):
    conn = connect_readonly(fixture_db)
    previous = conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    try:
        result = execute_on_connection(conn, "SELECT :x", {"x": "界" * 700},
                                       limits=limits(max_value_bytes=2000))
        assert result.error_code == "resource_limit"
        assert conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == previous
        assert execute_on_connection(conn, "SELECT :x", {"x": "界" * 700}).status == "ok"
        sql = "SELECT '界' AS x"
        assert execute_on_connection(conn, sql, limits=limits(max_sql_chars=len(sql))).status == "ok"
    finally:
        conn.close()


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), -1, 0])
def test_deadline_must_be_finite_positive(timeout):
    with pytest.raises(ValidationError):
        limits(timeout_seconds=timeout)


def test_cli_plan_errors_never_leak_path_or_value(tmp_path, capsys):
    missing = tmp_path / "private-marker.json"
    assert main(["--plan", str(missing)]) == 1
    assert "private-marker" not in capsys.readouterr().out
    missing.write_bytes(b"\xff")
    assert main(["--plan", str(missing)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_plan"
    missing.write_text(json.dumps({**plan(), "secret": "private-marker"}), encoding="utf-8")
    assert main(["--plan", str(missing)]) == 1
    assert "private-marker" not in capsys.readouterr().out


def test_filter_metadata_is_redacted_and_dimension_ranking_is_accurate(fixture_db):
    result = run_analysis_plan(plan(filters=[dict(dimension_id="region", op="eq", value="华东")],
                                    order_by="dimension_value", top_n=2), fixture_db)
    assert "华东" not in result.model_dump_json()
    assert result.filters == ({"dimension_id": "region", "op": "eq"},)
    assert "按指标值" not in result.completeness.note_zh
    assert not result.completeness.reaggregation_safe


def test_legacy_report_refuses_executor_truncation(fixture_db, monkeypatch):
    from eda.metrics import MetricFilters, compute_breakdown
    from eda.sql import executor
    original = executor.execute_on_connection
    monkeypatch.setattr(executor, "execute_on_connection",
                        lambda conn, sql, params: original(conn, sql, params, limits=limits(max_rows=1)))
    conn = connect_readonly(fixture_db)
    try:
        with pytest.raises(QueryError, match="resource_limit"):
            compute_breakdown(conn, "category", MetricFilters(start_date="2024-01-01", end_date="2024-12-31"))
    finally:
        conn.close()


def test_legacy_report_cli_handles_executor_error(fixture_db, monkeypatch, capsys):
    from eda.metrics import report

    def fail(*args, **kwargs):
        raise QueryError("resource_limit", "legacy report cannot represent truncated results")

    monkeypatch.setattr(report, "compute_breakdown", fail)
    assert report.main(["--db", str(fixture_db), "--by", "category"]) == 1
    captured = capsys.readouterr()
    assert "resource_limit" in captured.out
    assert "Traceback" not in captured.err
