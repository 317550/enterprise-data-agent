"""Access to the DDL that lives in ``schema.sql``.

The SQL is kept in a real .sql file so it stays readable and diff-friendly;
this module is just the loader plus the list of objects the DDL must create.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Base tables, in dependency order -- also the safe insert order.
EXPECTED_TABLES: tuple[str, ...] = ("customers", "products", "orders", "order_items")

EXPECTED_VIEWS: tuple[str, ...] = ("v_order_lines", "v_revenue_lines", "v_revenue_orders")


def read_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


SCHEMA_SQL: str = read_schema_sql()
