"""Read-only SQL execution strategy.

Opens connections only through :mod:`eda.db`. Every statement is validated by
the SQLGlot checker, then run under the default-deny authorizer. Resource
limits here are in-process best effort: they are not a hard OS sandbox.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eda.config import get_settings
from eda.db import DatabaseError, connect_readonly
from eda.query.errors import QueryError
from eda.sql.authorizer import clear_query_authorizer, install_query_authorizer
from eda.sql.validator import validate_sql

_SAFE_DB_UNAVAILABLE = "business database was not found or could not be opened"
_SAFE_DB_REJECTED = "the database rejected this statement"
_SAFE_INVALID_PARAMS = "SQL parameters are missing or invalid"
_SAFE_TIMEOUT = "SQL execution exceeded the time limit"
_SAFE_RESOURCE = "SQLite rejected the statement as too large"
_SAFE_UNAUTHORIZED = "SQLite authorizer denied this statement"
_SAFE_SQL_TOO_LONG = "SQL text exceeds the configured character budget"


class ExecutionLimits(BaseModel):
    """Conservative in-process budgets for one statement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_sql_chars: int = Field(gt=0)
    max_rows: int = Field(gt=0)
    max_result_bytes: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    #: Maps to ``SQLITE_LIMIT_LENGTH``, which is a byte budget, not a character count.
    max_value_bytes: int = Field(gt=0)

    @classmethod
    def from_settings(cls) -> ExecutionLimits:
        settings = get_settings()
        return cls(
            max_sql_chars=settings.sql_max_sql_chars,
            max_rows=settings.sql_max_rows,
            max_result_bytes=settings.sql_max_result_bytes,
            timeout_seconds=settings.sql_timeout_seconds,
            max_value_bytes=settings.sql_max_value_bytes,
        )


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str
    status: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    row_count: int
    elapsed_ms: float
    truncated: bool
    truncation_reason: str | None
    error_code: str | None = None
    error_message: str | None = None


def execute_readonly_query(
    db_path: str | Path,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any] | None = None,
    limits: ExecutionLimits | None = None,
) -> ExecutionResult:
    """Open a fresh read-only connection, run one query, then close it."""
    started = time.monotonic()
    try:
        conn = connect_readonly(db_path)
    except (DatabaseError, sqlite3.Error, OSError):
        return _failed_result(
            uuid.uuid4().hex,
            sql,
            started,
            "db_error",
            _SAFE_DB_UNAVAILABLE,
        )
    try:
        return execute_on_connection(conn, sql, params, limits=limits)
    finally:
        conn.close()


def execute_on_connection(
    conn: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any] | None = None,
    limits: ExecutionLimits | None = None,
) -> ExecutionResult:
    """Validate and execute ``sql`` on an existing connection.

    The caller owns the connection lifetime. Authorizer and progress handler
    are installed for this call only and always cleared afterwards.
    The caller must provide exclusive use and no existing callbacks: Python
    sqlite3 cannot retrieve/restore them. Engine limits are restored on return.
    Production callers must use the mode=ro factory in eda.db.
    """
    bounds = limits or ExecutionLimits.from_settings()
    query_id = uuid.uuid4().hex
    started = time.monotonic()
    bound = params if params is not None else {}
    try:
        result = _run(conn, sql, bound, bounds, query_id, started)
    except QueryError as exc:
        return _failed_result(query_id, sql, started, exc.code, exc.message)
    except DatabaseError:
        return _failed_result(query_id, sql, started, "db_error", _SAFE_DB_REJECTED)
    except sqlite3.Error as exc:
        try:
            _raise_engine_error(exc, started + bounds.timeout_seconds)
        except QueryError as classified:
            return _failed_result(query_id, sql, started, classified.code, classified.message)
    return result


def _run(
    conn: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any],
    limits: ExecutionLimits,
    query_id: str,
    started: float,
) -> ExecutionResult:
    if len(sql) > limits.max_sql_chars:
        raise QueryError("resource_limit", _SAFE_SQL_TOO_LONG)
    validate_sql(sql)
    deadline = started + limits.timeout_seconds

    def _progress() -> int:
        return 1 if time.monotonic() >= deadline else 0

    cursor: sqlite3.Cursor | None = None
    previous_limits = {
        category: conn.getlimit(category)
        for category in (sqlite3.SQLITE_LIMIT_SQL_LENGTH, sqlite3.SQLITE_LIMIT_LENGTH)
    }
    try:
        try:
            install_query_authorizer(conn)
            _apply_engine_limits(conn, limits)
            conn.set_progress_handler(_progress, 64)
            cursor = conn.execute(sql, params)
            assert cursor is not None
            columns = tuple(description[0] for description in (cursor.description or ()))
            rows, truncated, reason = _read_rows(cursor, limits, deadline)
            return ExecutionResult(
                query_id=query_id,
                status="ok",
                sql=sql,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_ms=_elapsed_ms(started),
                truncated=truncated,
                truncation_reason=reason,
            )
        except sqlite3.Error as exc:
            _raise_engine_error(exc, deadline)
        except (OverflowError, UnicodeError) as exc:
            raise QueryError("invalid_params", _SAFE_INVALID_PARAMS) from exc
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            try:
                conn.set_progress_handler(None, 0)
            finally:
                try:
                    clear_query_authorizer(conn)
                finally:
                    for category, value in previous_limits.items():
                        conn.setlimit(category, value)
    raise AssertionError("SQLite error must be classified before returning")


def _read_rows(
    cursor: sqlite3.Cursor,
    limits: ExecutionLimits,
    deadline: float,
) -> tuple[tuple[tuple[object, ...], ...], bool, str | None]:
    collected: list[tuple[object, ...]] = []
    used_bytes = 0
    while len(collected) < limits.max_rows:
        _raise_if_deadline(deadline)
        raw = cursor.fetchone()
        if raw is None:
            return tuple(collected), False, None
        row = tuple(raw)
        size = _row_utf8_bytes(row)
        if used_bytes + size > limits.max_result_bytes:
            return tuple(collected), True, "max_bytes"
        collected.append(row)
        used_bytes += size
    _raise_if_deadline(deadline)
    extra = cursor.fetchone()
    if extra is None:
        return tuple(collected), False, None
    return tuple(collected), True, "max_rows"


def _row_utf8_bytes(row: tuple[object, ...]) -> int:
    """UTF-8 size of the JSON array of this row's values.

    This is the billed payload for ``max_result_bytes``. A row that would
    exceed the budget is not added to the result.
    """
    return len(json.dumps([_cell(value) for value in row], ensure_ascii=False).encode("utf-8"))


def _cell(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _apply_engine_limits(conn: sqlite3.Connection, limits: ExecutionLimits) -> None:
    setter = getattr(conn, "setlimit", None)
    if setter is None:
        return
    # SQLite counts UTF-8 bytes; Python's public SQL budget counts characters.
    setter(sqlite3.SQLITE_LIMIT_SQL_LENGTH, min(limits.max_sql_chars * 4, 2**31 - 1))
    setter(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)


def _raise_engine_error(exc: sqlite3.Error, deadline: float) -> None:
    message = str(exc).lower()
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if code == sqlite3.SQLITE_INTERRUPT or "interrupt" in message:
        raise QueryError("timeout", _SAFE_TIMEOUT) from exc
    if _is_binding_error(message):
        raise QueryError("invalid_params", _SAFE_INVALID_PARAMS) from exc
    if code in {sqlite3.SQLITE_TOOBIG, sqlite3.SQLITE_NOMEM, sqlite3.SQLITE_FULL} or _is_sqlite_resource_error(message):
        raise QueryError("resource_limit", _SAFE_RESOURCE) from exc
    if code == sqlite3.SQLITE_AUTH or "authoriz" in message or "not authorized" in message or "prohibited" in message:
        raise QueryError("unauthorized", _SAFE_UNAUTHORIZED) from exc
    raise QueryError("db_error", _SAFE_DB_REJECTED) from exc


def _is_binding_error(message: str) -> bool:
    return (
        "binding parameter" in message
        or "binding" in message and "supplied" in message
        or "parameters are of unsupported type" in message
        or "bindings supplied" in message
        or "error binding" in message
    )


def _is_sqlite_resource_error(message: str) -> bool:
    if "too big" in message or "too long" in message or "too much sql" in message:
        return True
    return "too many" in message and (
        "variable" in message or "term" in message or "column" in message
    )


def _raise_if_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise QueryError("timeout", _SAFE_TIMEOUT)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)


def _failed_result(
    query_id: str,
    sql: str,
    started: float,
    code: str,
    message: str,
) -> ExecutionResult:
    return ExecutionResult(
        query_id=query_id,
        status="error",
        sql=sql,
        columns=(),
        rows=(),
        row_count=0,
        elapsed_ms=_elapsed_ms(started),
        truncated=False,
        truncation_reason=None,
        error_code=code,
        error_message=message,
    )


def require_ok(result: ExecutionResult) -> ExecutionResult:
    """Raise if an execution result is a classified failure."""
    if result.status == "ok":
        return result
    raise QueryError(result.error_code or "db_error", result.error_message or "query failed")
