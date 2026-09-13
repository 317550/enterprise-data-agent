"""SQLGlot validator: structure and object access, not business 口径."""

from __future__ import annotations

import pytest

from eda.query.errors import QueryError
from eda.sql.validator import validate_sql


def test_compiler_shaped_select_is_accepted() -> None:
    validate_sql(
        "SELECT COALESCE(SUM(line_amount_cents), 0) AS metric_value "
        "FROM v_revenue_lines "
        "WHERE order_date >= :start_date AND order_date <= :end_date"
    )
    validate_sql(
        "SELECT category AS dimension_value, "
        "COALESCE(SUM(line_amount_cents), 0) AS metric_value "
        "FROM v_revenue_lines GROUP BY category "
        "ORDER BY metric_value DESC NULLS LAST, dimension_value ASC "
        "LIMIT :top_n"
    )


def test_count_star_and_explicit_joins_are_accepted() -> None:
    validate_sql("SELECT COUNT(*) AS c FROM orders")
    validate_sql(
        "SELECT a.order_id FROM orders AS a "
        "INNER JOIN customers AS c ON a.customer_id = c.customer_id"
    )
    validate_sql(
        "SELECT o.order_id FROM orders AS o "
        "LEFT JOIN customers AS c ON o.customer_id = c.customer_id"
    )


def test_non_recursive_cte_is_accepted() -> None:
    validate_sql("WITH t AS (SELECT order_id FROM orders) SELECT order_id FROM t")


def test_select_star_is_rejected() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT * FROM orders")
    assert exc.value.code == "unsupported_sql"


def test_multiple_statements_and_comment_confusion() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT 1; SELECT 2")
    assert exc.value.code == "unsupported_sql"
    validate_sql("SELECT 'a;b' AS x FROM orders")
    validate_sql("SELECT order_id FROM orders -- trailing; DROP TABLE orders")


def test_recursive_cte_and_set_operations_are_rejected() -> None:
    with pytest.raises(QueryError):
        validate_sql(
            "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n<3) "
            "SELECT n FROM t"
        )
    with pytest.raises(QueryError):
        validate_sql("SELECT order_id FROM orders UNION SELECT order_id FROM orders")


def test_offset_is_rejected() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT order_id FROM orders LIMIT 1 OFFSET 1")
    assert exc.value.code == "unsupported_sql"


def test_window_functions_are_rejected() -> None:
    with pytest.raises(QueryError):
        validate_sql("SELECT SUM(quantity) OVER (PARTITION BY order_id) FROM order_items")


def test_writes_ddl_pragma_and_attach_are_rejected() -> None:
    for sql in (
        "INSERT INTO orders VALUES (1)",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'paid'",
        "DROP TABLE orders",
        "CREATE TABLE evil (x INTEGER)",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE 'x.db' AS evil",
        "DETACH DATABASE evil",
    ):
        with pytest.raises(QueryError):
            validate_sql(sql)


def test_system_table_and_unknown_relation_are_unauthorized() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT name FROM sqlite_master")
    assert exc.value.code == "unauthorized"
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT x FROM checkpoints")
    assert exc.value.code == "unauthorized"


def test_dangerous_function_is_rejected() -> None:
    with pytest.raises(QueryError):
        validate_sql("SELECT load_extension('x')")


def test_cte_cannot_shadow_a_physical_table() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("WITH orders AS (SELECT order_id FROM customers) SELECT order_id FROM orders")
    assert exc.value.code == "unauthorized"


def test_cte_shadowing_is_case_insensitive() -> None:
    for sql in (
        "WITH Orders AS (SELECT order_id FROM customers) SELECT order_id FROM Orders",
        "WITH ORDERS AS (SELECT order_id FROM customers) SELECT order_id FROM ORDERS",
    ):
        with pytest.raises(QueryError) as exc:
            validate_sql(sql)
        assert exc.value.code == "unauthorized"


def test_physical_relation_names_are_accepted_in_any_case() -> None:
    validate_sql("SELECT order_id FROM ORDERS")
    validate_sql("SELECT ORDER_ID FROM Orders")


def test_duplicate_aliases_are_rejected_even_with_different_case() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql(
            "SELECT a.order_id FROM orders AS a "
            "INNER JOIN customers AS A ON a.customer_id = A.customer_id"
        )
    assert exc.value.code == "unsupported_sql"


def test_join_using_is_rejected() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql(
            "SELECT orders.order_id FROM orders "
            "INNER JOIN customers USING (customer_id)"
        )
    assert exc.value.code == "unsupported_sql"
    assert "USING" in exc.value.message


def test_unqualified_join_column_is_rejected_when_ambiguous() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql(
            "SELECT customer_id FROM orders "
            "INNER JOIN customers ON orders.customer_id = customers.customer_id"
        )
    assert exc.value.code == "unsupported_sql"


def test_unknown_column_is_unauthorized() -> None:
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT secret_column FROM orders")
    assert exc.value.code == "unauthorized"
