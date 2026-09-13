"""The one and only place in this project that talks to SQLite.

Why this module is small and boring on purpose
----------------------------------------------
The project has a hard constraint: there must be exactly ONE path that opens a
SQLite connection. Stage 2's safe executor calls the factories below; it must
not call ``sqlite3.connect`` itself.

Read-only means read-only twice over:
  1. the connection is opened with a correctly encoded ``mode=ro`` URI, so
     SQLite itself refuses writes and will not create a missing file;
  2. ``PRAGMA query_only = ON`` is set as defence in depth.

``sqlite3.connect(..., timeout=)`` is the lock-wait budget, not a SQL
execution timeout. Execution time limits live in :mod:`eda.sql.executor`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: STRICT tables were introduced in SQLite 3.37.0.
MIN_SQLITE_VERSION: tuple[int, int, int] = (3, 37, 0)


class DatabaseError(RuntimeError):
    """Raised for project-level database problems (missing file, old SQLite...)."""


def sqlite_version() -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in sqlite3.sqlite_version.split(".")[:3])
    return major, minor, patch


def require_supported_sqlite() -> None:
    """Fail with an actionable message instead of a cryptic syntax error."""
    if sqlite_version() < MIN_SQLITE_VERSION:
        need = ".".join(str(p) for p in MIN_SQLITE_VERSION)
        raise DatabaseError(
            f"this project needs SQLite >= {need} for STRICT tables, "
            f"but the running Python is linked against {sqlite3.sqlite_version}"
        )


def readonly_uri(db_path: str | Path) -> str:
    """Build a ``file:`` URI with ``mode=ro`` and percent-encoded path parts."""
    path = Path(db_path)
    if not path.is_file():
        raise DatabaseError(
            f"business database not found: {path}. "
            "Build it first with: python -m eda.data.build_db --dataset demo"
        )
    return f"{path.resolve().as_uri()}?mode=ro"


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _disable_extensions(conn: sqlite3.Connection) -> None:
    enable = getattr(conn, "enable_load_extension", None)
    if enable is None:
        return
    try:
        enable(False)
    except sqlite3.OperationalError:
        return


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the business database read-only.

    Raises :class:`DatabaseError` if the file does not exist, because the
    ``mode=ro`` URI would otherwise fail with a less obvious message and must
    never create an empty database.
    """
    uri = readonly_uri(db_path)
    # timeout is lock-wait only; it is not a statement deadline.
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        _configure(conn)
        _disable_extensions(conn)
        conn.execute("PRAGMA query_only = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def connect_for_build(db_path: str | Path) -> sqlite3.Connection:
    """Open a writable connection. Only ``eda.data.build_db`` may use this.

    The agent never calls this, and it is never pointed at the checkpoint
    database -- LangGraph owns that file (stage 4).
    """
    require_supported_sqlite()
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    _disable_extensions(conn)
    return _configure(conn)


def fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any] | None = None,
) -> list[sqlite3.Row]:
    """Execute one trusted statement and return all rows.

    Runtime analysis queries must use :func:`eda.sql.executor.execute_on_connection`
    instead. This helper remains for schema introspection and the build path.
    """
    cursor = conn.execute(sql, params if params is not None else {})
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any] | None = None,
) -> sqlite3.Row | None:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = fetch_all(
        conn,
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name",
    )
    return [row["name"] for row in rows]


def view_names(conn: sqlite3.Connection) -> list[str]:
    rows = fetch_all(
        conn, "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name"
    )
    return [row["name"] for row in rows]
