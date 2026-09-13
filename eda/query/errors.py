"""Structured errors for the stage 2 query pipeline.

These codes are the only failure vocabulary the CLI and tests should rely on.
Empty query results are not errors; they travel as successful rows=().
"""

from __future__ import annotations


class QueryError(Exception):
    """A classified, safe-to-display query failure."""

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown query error code {code!r}")
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_plan",
        "sql_parse",
        "unsupported_sql",
        "unauthorized",
        "invalid_params",
        "timeout",
        "resource_limit",
        "db_error",
    }
)
