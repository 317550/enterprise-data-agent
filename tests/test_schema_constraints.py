"""The database must refuse bad data by itself, not rely on Python being careful."""

from __future__ import annotations

import sqlite3

import pytest

from eda.data.schema import EXPECTED_TABLES, EXPECTED_VIEWS
from eda.db import fetch_all, fetch_one, table_names, view_names

_VALID_CUSTOMER = {
    "customer_id": "C900",
    "signup_date": "2024-01-01",
    "signup_region": "华东",
    "signup_channel": "web",
    "customer_tier": "new",
}
_VALID_PRODUCT = {
    "product_id": "P900",
    "product_name": "探针商品",
    "category": "家居日用",
    "list_price_cents": 1000,
    "is_active": 1,
}
_VALID_ORDER = {
    "order_id": "O900",
    "customer_id": "C900",
    "order_date": "2024-01-02",
    "status": "paid",
    "order_region": "华东",
    "order_channel": "web",
}


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO customers VALUES "
        "(:customer_id, :signup_date, :signup_region, :signup_channel, :customer_tier)",
        _VALID_CUSTOMER,
    )
    conn.execute(
        "INSERT INTO products VALUES "
        "(:product_id, :product_name, :category, :list_price_cents, :is_active)",
        _VALID_PRODUCT,
    )
    conn.execute(
        "INSERT INTO orders VALUES "
        "(:order_id, :customer_id, :order_date, :status, :order_region, :order_channel)",
        _VALID_ORDER,
    )
    conn.commit()


def test_expected_tables_and_views_exist(fixture_conn: sqlite3.Connection) -> None:
    assert table_names(fixture_conn) == sorted(EXPECTED_TABLES)
    assert view_names(fixture_conn) == sorted(EXPECTED_VIEWS)


def test_tables_are_strict(fixture_conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        fixture_conn,
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )
    for row in rows:
        assert "STRICT" in row["sql"].upper(), f"{row['name']} is not a STRICT table"


def test_primary_keys_and_unique_constraints(fixture_conn: sqlite3.Connection) -> None:
    for table, key in (
        ("customers", "customer_id"),
        ("products", "product_id"),
        ("orders", "order_id"),
        ("order_items", "order_item_id"),
    ):
        row = fetch_one(
            fixture_conn,
            f"SELECT COUNT(*) AS total, COUNT(DISTINCT {key}) AS distinct_keys FROM {table}",
        )
        assert row is not None
        assert row["total"] == row["distinct_keys"], f"{table}.{key} is not unique"


def test_no_foreign_key_violations_in_built_databases(
    fixture_conn: sqlite3.Connection, demo_conn: sqlite3.Connection
) -> None:
    assert fetch_all(fixture_conn, "PRAGMA foreign_key_check") == []
    assert fetch_all(demo_conn, "PRAGMA foreign_key_check") == []


def test_foreign_keys_are_enforced(empty_writable_db: sqlite3.Connection) -> None:
    assert fetch_one(empty_writable_db, "PRAGMA foreign_keys")[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        empty_writable_db.execute(
            "INSERT INTO orders VALUES ('O999', 'C-does-not-exist', '2024-01-01', "
            "'paid', '华东', 'web')"
        )


def test_order_item_requires_existing_order_and_product(
    empty_writable_db: sqlite3.Connection,
) -> None:
    _seed(empty_writable_db)
    with pytest.raises(sqlite3.IntegrityError):
        empty_writable_db.execute(
            "INSERT INTO order_items VALUES ('X-1', 'O-nope', 'P900', 1, 100)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        empty_writable_db.execute(
            "INSERT INTO order_items VALUES ('X-2', 'O900', 'P-nope', 1, 100)"
        )


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        (
            "INSERT INTO orders VALUES ('O901','C900','2024-01-02','shipped','华东','web')",
            "unknown status",
        ),
        (
            "INSERT INTO orders VALUES ('O902','C900','2024-1-2','paid','华东','web')",
            "non zero-padded date",
        ),
        (
            "INSERT INTO orders VALUES ('O903','C900','2024-01-02','paid','火星','web')",
            "unknown region",
        ),
        (
            "INSERT INTO order_items VALUES ('O900-1','O900','P900',0,100)",
            "quantity must be > 0",
        ),
        (
            "INSERT INTO order_items VALUES ('O900-2','O900','P900',-1,100)",
            "negative quantity",
        ),
        (
            "INSERT INTO order_items VALUES ('O900-3','O900','P900',1,-5)",
            "negative unit price",
        ),
        (
            "INSERT INTO order_items VALUES ('O900-4','O900','P900',1,'99.5')",
            "money must be an integer number of cents (STRICT table)",
        ),
        (
            "INSERT INTO products VALUES ('P901','x','未知类别',100,1)",
            "unknown category",
        ),
        (
            "INSERT INTO products VALUES ('P902','x','家居日用',0,1)",
            "list price must be > 0",
        ),
    ],
)
def test_check_constraints_reject_bad_rows(
    empty_writable_db: sqlite3.Connection, sql: str, reason: str
) -> None:
    _seed(empty_writable_db)
    with pytest.raises(sqlite3.DatabaseError):
        empty_writable_db.execute(sql)


def test_same_product_cannot_appear_twice_in_one_order(
    empty_writable_db: sqlite3.Connection,
) -> None:
    _seed(empty_writable_db)
    empty_writable_db.execute("INSERT INTO order_items VALUES ('O900-1','O900','P900',1,100)")
    with pytest.raises(sqlite3.IntegrityError):
        empty_writable_db.execute(
            "INSERT INTO order_items VALUES ('O900-2','O900','P900',2,100)"
        )


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE order_items SET quantity = quantity + 1",
        "INSERT INTO products VALUES ('P998','x','家居日用',100,1)",
        "DROP VIEW v_revenue_lines",
        "CREATE TABLE evil (x INTEGER)",
    ],
)
def test_readonly_connection_rejects_writes(
    fixture_conn: sqlite3.Connection, sql: str
) -> None:
    """mode=ro plus PRAGMA query_only: the connection itself cannot mutate anything."""
    with pytest.raises(sqlite3.DatabaseError):
        fixture_conn.execute(sql)


def test_readonly_survives_query_only_being_switched_off(fixture_db) -> None:
    """Defence in depth: the mode=ro URI holds even if query_only is disabled.

    Uses its own connection so the session-scoped one keeps its pragma intact.
    """
    from eda.db import connect_readonly

    conn = connect_readonly(fixture_db)
    try:
        conn.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM orders")
    finally:
        conn.close()


def test_line_amount_is_derived_not_stored(fixture_conn: sqlite3.Connection) -> None:
    """order_items has no amount column, so it can never disagree with the views."""
    columns = {
        row["name"] for row in fetch_all(fixture_conn, "PRAGMA table_info(order_items)")
    }
    assert "line_amount_cents" not in columns
    mismatches = fetch_all(
        fixture_conn,
        "SELECT l.order_item_id FROM v_order_lines AS l "
        "JOIN order_items AS i ON i.order_item_id = l.order_item_id "
        "WHERE l.line_amount_cents <> i.quantity * i.unit_price_cents",
    )
    assert mismatches == []
