"""The one and only place in this project that talks to SQLite.

Why this module is small and boring on purpose
----------------------------------------------
The project has a hard constraint: there must be exactly ONE path that executes
SQL against the business database. Stage 2 adds the validating safe executor on
top of :func:`fetch_all`; from that point on, application and agent code must go
through the executor and nothing else may import :func:`fetch_all` directly.
Keeping every ``sqlite3.connect`` call here is what makes that rule checkable.

Read-only means read-only twice over:
  1. the connection is opened with the ``mode=ro`` URI flag, so SQLite itself
     refuses writes and will not create a missing file;
  2. ``PRAGMA query_only = ON`` is set as defence in depth.
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


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the business database read-only.

    Raises :class:`DatabaseError` if the file does not exist, because the
    ``mode=ro`` URI would otherwise fail with a less obvious message.
    """
    path = Path(db_path)
    if not path.is_file():
        raise DatabaseError(
            f"business database not found: {path}. "
            "Build it first with: python -m eda.data.build_db --dataset demo"
        )
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    _configure(conn)
    conn.execute("PRAGMA query_only = ON")
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
    return _configure(conn)


def fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any] | Sequence[Any] | None = None,
) -> list[sqlite3.Row]:
    """Execute one statement and return all rows.

    This is the single execution primitive. Parameters are always bound, never
    formatted into the SQL string.
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
