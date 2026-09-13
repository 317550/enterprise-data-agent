"""SQLGlot AST validator: is this SQL structurally and object-safe?

This is not a business-口径 check and not the last defence. The authorizer
still runs at execution time. Unrecognised syntax is denied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from eda.query.errors import QueryError
from eda.sql.catalog import (
    APPROVED_FUNCTIONS,
    APPROVED_RELATIONS_FOLD,
    approved_columns,
    fold_ident,
    is_system_table,
)

_ALLOWED_JOIN_KINDS = frozenset({"", "INNER", "LEFT"})

_ALLOWED_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.From,
    exp.Where,
    exp.Group,
    exp.Order,
    exp.Limit,
    exp.Join,
    exp.Table,
    exp.TableAlias,
    exp.Alias,
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Null,
    exp.Boolean,
    exp.Placeholder,
    exp.And,
    exp.Or,
    exp.Not,
    exp.Paren,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.In,
    exp.Is,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Coalesce,
    exp.Sum,
    exp.Count,
    exp.Substring,
    exp.Distinct,
    exp.Case,
    exp.If,
    exp.Star,
    exp.Ordered,
    exp.With,
    exp.CTE,
)


def validate_sql(sql: str) -> None:
    """Parse ``sql`` as SQLite and reject anything this stage cannot vouch for."""
    try:
        _validate_sql(sql)
    except RecursionError as exc:
        raise QueryError("unsupported_sql", "SQL nesting exceeds the supported depth") from exc


def _validate_sql(sql: str) -> None:
    if not sql or not sql.strip():
        raise QueryError("sql_parse", "SQL text is empty")
    try:
        trees = sqlglot.parse(sql, dialect="sqlite")
    except (ParseError, TokenError) as exc:
        raise QueryError("sql_parse", "SQL could not be parsed") from exc

    statements = [tree for tree in trees if tree is not None]
    if len(statements) != 1:
        raise QueryError(
            "unsupported_sql",
            "only a single SELECT statement is allowed",
        )
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise QueryError(
            "unsupported_sql",
            "statement is not a supported SELECT",
        )
    _reject_unknown_nodes(tree)
    _reject_disallowed_shape(tree)
    _validate_select(tree, cte_columns={})


def _reject_unknown_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if type(node) not in _ALLOWED_NODE_TYPES:
            raise QueryError(
                "unsupported_sql",
                "unsupported SQL construct",
            )
        if isinstance(node, exp.Anonymous):
            raise QueryError(
                "unauthorized",
                "function is not on the allow-list",
            )
        if isinstance(node, exp.Func) and not isinstance(
            node,
            (
                exp.Coalesce,
                exp.Sum,
                exp.Count,
                exp.Substring,
                exp.And,
                exp.Or,
                exp.Not,
                exp.EQ,
                exp.NEQ,
                exp.GT,
                exp.GTE,
                exp.LT,
                exp.LTE,
                exp.In,
                exp.Is,
                exp.Add,
                exp.Sub,
                exp.Mul,
                exp.Div,
                exp.If,
                exp.Case,
            ),
        ):
            name = getattr(node, "sql_name", lambda: type(node).__name__)()
            if fold_ident(str(name)) not in APPROVED_FUNCTIONS:
                raise QueryError(
                    "unauthorized",
                    "function is not on the allow-list",
                )


def _reject_disallowed_shape(tree: exp.Select) -> None:
    with_ = tree.args.get("with_")
    if with_ is not None and with_.args.get("recursive"):
        raise QueryError("unsupported_sql", "recursive CTE is not allowed")
    if tree.args.get("distinct"):
        raise QueryError("unsupported_sql", "SELECT DISTINCT is not allowed")
    if tree.args.get("having"):
        raise QueryError("unsupported_sql", "HAVING is not allowed")
    if tree.args.get("qualify"):
        raise QueryError("unsupported_sql", "QUALIFY is not allowed")
    if tree.find(exp.Window):
        raise QueryError("unsupported_sql", "window functions are not allowed")
    if tree.find(exp.Union) or tree.find(exp.Intersect) or tree.find(exp.Except):
        raise QueryError("unsupported_sql", "set operations are not allowed")
    for star in tree.find_all(exp.Star):
        parent = star.parent
        if not isinstance(parent, exp.Count):
            raise QueryError(
                "unsupported_sql",
                "SELECT * is not allowed; COUNT(*) is allowed",
            )
    for func in tree.find_all((exp.Coalesce, exp.Sum, exp.Count, exp.Substring, exp.Anonymous)):
        name = fold_ident(func.sql_name() if hasattr(func, "sql_name") else type(func).__name__)
        if isinstance(func, exp.Anonymous):
            name = fold_ident(func.name)
        elif isinstance(func, exp.Coalesce):
            name = "coalesce"
        elif isinstance(func, exp.Sum):
            name = "sum"
        elif isinstance(func, exp.Count):
            name = "count"
        elif isinstance(func, exp.Substring):
            name = "substr"
        if fold_ident(name) not in APPROVED_FUNCTIONS:
            raise QueryError("unauthorized", "function is not on the allow-list")


def _validate_select(select: exp.Select, cte_columns: dict[str, set[str]]) -> None:
    _reject_disallowed_shape(select)
    local_ctes = dict(cte_columns)
    with_ = select.args.get("with_")
    if with_ is not None:
        for cte in with_.expressions:
            name = fold_ident(cte.alias_or_name)
            if name in local_ctes or cte.args["alias"].args.get("columns"):
                raise QueryError("unsupported_sql", "duplicate or renamed CTE columns are not supported")
            if _is_forbidden_relation_name(name):
                raise QueryError(
                    "unauthorized",
                    "CTE cannot shadow a physical table, view or system table",
                )
            inner = cte.this
            if not isinstance(inner, exp.Select):
                raise QueryError("unsupported_sql", "CTE body must be a SELECT")
            _validate_select(inner, local_ctes)
            local_ctes[name] = _output_columns(inner)

    scope = _scope_from_from_clause(select, local_ctes)
    for projection in select.expressions:
        _validate_expr(projection, scope, allow_aliases=False)
    if select.args.get("where"):
        _validate_expr(select.args["where"].this, scope, allow_aliases=False)
    if select.args.get("group"):
        for item in select.args["group"].expressions:
            _validate_expr(item, scope, allow_aliases=True)
    if select.args.get("order"):
        for item in select.args["order"].expressions:
            target = item.this if isinstance(item, exp.Ordered) else item
            _validate_expr(target, scope, allow_aliases=True)
    if select.args.get("limit"):
        limit_node = select.args["limit"]
        # SQLGlot 30 stores LIMIT :name on args['expression'] and leaves .this empty.
        limit_expr = limit_node.this or limit_node.args.get("expression")
        if limit_expr is None:
            raise QueryError("unsupported_sql", "LIMIT must be a literal or parameter")
        _validate_expr(limit_expr, scope, allow_aliases=False)
    if select.args.get("offset"):
        raise QueryError("unsupported_sql", "OFFSET is not allowed")


@dataclass
class _Scope:
    """Visible relations in one SELECT: physical tables, views, aliases, CTEs."""

    sources: dict[str, str] = field(default_factory=dict)
    columns_by_source: dict[str, set[str]] = field(default_factory=dict)
    output_aliases: set[str] = field(default_factory=set)

    def add_source(self, alias: str, relation: str, columns: set[str]) -> None:
        folded_alias = fold_ident(alias)
        if folded_alias in self.sources:
            raise QueryError("unsupported_sql", "duplicate table alias")
        self.sources[folded_alias] = fold_ident(relation)
        self.columns_by_source[folded_alias] = {fold_ident(column) for column in columns}


def _scope_from_from_clause(
    select: exp.Select, cte_columns: dict[str, set[str]]
) -> _Scope:
    scope = _Scope()
    from_ = select.args.get("from_")
    if from_ is None:
        # Literal-only SELECT is unused by the compiler; still validate projections.
        return scope
    _add_source(scope, from_.this, cte_columns)
    for join in select.args.get("joins") or []:
        if not isinstance(join, exp.Join):
            raise QueryError("unsupported_sql", "unsupported JOIN form")
        kind = (join.args.get("kind") or "").upper()
        if kind not in _ALLOWED_JOIN_KINDS:
            raise QueryError(
                "unsupported_sql",
                "JOIN kind is not allowed",
            )
        if join.args.get("method") or join.args.get("side") in {"RIGHT", "FULL"}:
            raise QueryError("unsupported_sql", "only INNER JOIN and LEFT JOIN are allowed")
        if join.args.get("using"):
            raise QueryError(
                "unsupported_sql",
                "JOIN ... USING is not allowed; use JOIN ... ON",
            )
        if not join.args.get("on"):
            raise QueryError("unsupported_sql", "JOIN must have an ON clause")
        _add_source(scope, join.this, cte_columns)
        _validate_expr(join.args["on"], scope, allow_aliases=False)
    scope.output_aliases = _output_columns(select)
    return scope


def _add_source(
    scope: _Scope, source: exp.Expression, cte_columns: dict[str, set[str]]
) -> None:
    if isinstance(source, exp.Alias) and isinstance(source.this, exp.Table):
        source = source.this
    if not isinstance(source, exp.Table):
        raise QueryError(
            "unsupported_sql",
            "FROM/JOIN must name a table, view or CTE; derived tables are not allowed",
        )
    if source.args.get("db") or source.args.get("catalog"):
        raise QueryError("unauthorized", "qualified database names are not allowed")
    name = fold_ident(source.name)
    alias = fold_ident(source.alias or source.name)
    if is_system_table(name):
        raise QueryError("unauthorized", "system table is not allowed")
    if name in cte_columns:
        scope.add_source(alias, name, set(cte_columns[name]))
        return
    columns = approved_columns(name)
    if columns is None:
        raise QueryError("unauthorized", "relation is not approved")
    scope.add_source(alias, name, set(columns))


def _output_columns(select: exp.Select) -> set[str]:
    names: set[str] = set()
    for projection in select.expressions:
        if isinstance(projection, exp.Alias):
            names.add(fold_ident(projection.alias))
        elif isinstance(projection, exp.Column):
            names.add(fold_ident(projection.name))
        elif isinstance(projection, exp.Count) and isinstance(projection.this, exp.Star):
            names.add("count")
    return names


def _validate_expr(node: exp.Expression, scope: _Scope, *, allow_aliases: bool) -> None:
    if node is None:
        return
    if isinstance(node, exp.Column):
        _validate_column(node, scope, allow_aliases=allow_aliases)
        return
    if isinstance(node, exp.Ordered):
        _validate_expr(node.this, scope, allow_aliases=allow_aliases)
        return
    if isinstance(node, (exp.Literal, exp.Null, exp.Boolean, exp.Placeholder)):
        return
    if isinstance(node, exp.Star):
        return
    for child in node.iter_expressions():
        _validate_expr(child, scope, allow_aliases=allow_aliases)


def _validate_column(column: exp.Column, scope: _Scope, *, allow_aliases: bool) -> None:
    if column.args.get("db") or column.args.get("catalog"):
        raise QueryError("unauthorized", "qualified database names are not allowed")
    name = fold_ident(column.name)
    table = fold_ident(column.table) if column.table else ""
    if table:
        if table not in scope.sources:
            raise QueryError("unauthorized", "unknown table alias")
        if name not in scope.columns_by_source[table]:
            raise QueryError(
                "unauthorized",
                "column is not approved on this relation",
            )
        return
    owners = [
        alias for alias, columns in scope.columns_by_source.items() if name in columns
    ]
    if len(owners) > 1:
        raise QueryError(
            "unsupported_sql",
            "unqualified column is ambiguous across joined relations",
        )
    if len(owners) == 1:
        return
    if allow_aliases and name in scope.output_aliases:
        return
    if not scope.sources and allow_aliases:
        return
    raise QueryError("unauthorized", "column is not visible in this query")


def _is_forbidden_relation_name(name: str) -> bool:
    folded = fold_ident(name)
    return folded in APPROVED_RELATIONS_FOLD or is_system_table(folded)
