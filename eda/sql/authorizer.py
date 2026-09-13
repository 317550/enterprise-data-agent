"""SQLite authorizer: last-line defence for one connection.

Default deny. Only SELECT, approved READ of approved relations/columns on the
``main`` database, and approved functions are allowed. Denied operations use
SQLITE_DENY so the engine does not silently rewrite the query.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from eda.sql.catalog import APPROVED_FUNCTIONS, approved_columns, fold_ident, is_system_table

Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]

_MAIN_DB = "main"


def install_query_authorizer(conn: sqlite3.Connection) -> None:
    """Install on an exclusively owned connection with no existing callbacks.

    SQLite 3.45 can omit the database for column-free scans, including scans
    of attached/temp tables. Only a main-only connection can disambiguate it.
    The installed policy denies ATTACH and DDL, keeping this snapshot valid.
    """
    cursor = conn.execute("PRAGMA database_list")
    try:
        main_only = {row[1] for row in cursor.fetchall()} == {"main"}
    finally:
        cursor.close()

    def authorize(action, arg1, arg2, dbname, source):
        if main_only and action == sqlite3.SQLITE_READ and dbname is None and arg2 == "":
            dbname = _MAIN_DB
        return _authorize(action, arg1, arg2, dbname, source)

    conn.set_authorizer(authorize)


def clear_query_authorizer(conn: sqlite3.Connection) -> None:
    conn.set_authorizer(None)


def _authorize(
    action: int,
    arg1: str | None,
    arg2: str | None,
    dbname: str | None,
    _source: str | None,
) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        name = fold_ident(arg2 or "")
        if name in APPROVED_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        folded_db = fold_ident(dbname or "")
        if folded_db != _MAIN_DB:
            return sqlite3.SQLITE_DENY
        table = fold_ident(arg1 or "")
        column = fold_ident(arg2 or "")
        if is_system_table(table):
            return sqlite3.SQLITE_DENY
        approved = approved_columns(table)
        if approved is None:
            return sqlite3.SQLITE_DENY
        if column in {"", "*"} or column in approved:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY
