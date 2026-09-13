"""Write a validated :class:`~eda.domain.models.Dataset` into a fresh SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from eda.data.schema import EXPECTED_TABLES, EXPECTED_VIEWS, read_schema_sql
from eda.db import DatabaseError, connect_for_build, fetch_all
from eda.domain.models import Dataset

_INSERTS: dict[str, str] = {
    "customers": (
        "INSERT INTO customers "
        "(customer_id, signup_date, signup_region, signup_channel, customer_tier) "
        "VALUES (:customer_id, :signup_date, :signup_region, :signup_channel, :customer_tier)"
    ),
    "products": (
        "INSERT INTO products "
        "(product_id, product_name, category, list_price_cents, is_active) "
        "VALUES (:product_id, :product_name, :category, :list_price_cents, :is_active)"
    ),
    "orders": (
        "INSERT INTO orders "
        "(order_id, customer_id, order_date, status, order_region, order_channel) "
        "VALUES (:order_id, :customer_id, :order_date, :status, :order_region, :order_channel)"
    ),
    "order_items": (
        "INSERT INTO order_items "
        "(order_item_id, order_id, product_id, quantity, unit_price_cents) "
        "VALUES (:order_item_id, :order_id, :product_id, :quantity, :unit_price_cents)"
    ),
}


def _rows(dataset: Dataset, table: str) -> list[dict[str, object]]:
    records = getattr(dataset, table)
    return [record.model_dump(mode="json") for record in records]


def write_dataset(dataset: Dataset, db_path: str | Path) -> dict[str, int]:
    """Create the schema at ``db_path`` and insert ``dataset``.

    The file must not already contain the schema; use
    :mod:`eda.data.build_db` for the overwrite policy. Everything happens in one
    transaction, so a failure leaves no half-populated database behind.
    """
    conn = connect_for_build(db_path)
    try:
        conn.executescript(read_schema_sql())
        try:
            conn.execute("BEGIN")
            for table in EXPECTED_TABLES:  # dependency order: FKs always resolve
                conn.executemany(_INSERTS[table], _rows(dataset, table))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _verify(conn, dataset)
        conn.execute("PRAGMA optimize")
        return dataset.row_counts
    finally:
        conn.close()


def _verify(conn: sqlite3.Connection, dataset: Dataset) -> None:
    """Post-build assertions. A build that cannot be verified is a failed build."""
    violations = fetch_all(conn, "PRAGMA foreign_key_check")
    if violations:
        raise DatabaseError(f"foreign key violations after build: {len(violations)}")

    integrity = fetch_all(conn, "PRAGMA integrity_check")
    if not integrity or integrity[0][0] != "ok":
        raise DatabaseError(f"integrity_check failed: {integrity}")

    for table, expected in dataset.row_counts.items():
        # Table names come from EXPECTED_TABLES, never from user or model input.
        actual = fetch_all(conn, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        if actual != expected:
            raise DatabaseError(f"{table}: inserted {actual} rows, expected {expected}")

    present_views = {
        row["name"]
        for row in fetch_all(conn, "SELECT name FROM sqlite_master WHERE type = 'view'")
    }
    missing = set(EXPECTED_VIEWS) - present_views
    if missing:
        raise DatabaseError(f"schema did not create views: {sorted(missing)}")
