"""Approved relations and columns, derived from the semantic layer.

Used by the AST validator and the SQLite authorizer. This is not a second
metric formula: it only republishes names the semantic model already declared.
"""

from __future__ import annotations

from eda.data.schema import EXPECTED_TABLES, EXPECTED_VIEWS
from eda.semantic import load_semantic_model

SEMANTIC = load_semantic_model()

SYSTEM_TABLES: frozenset[str] = frozenset(
    {
        "sqlite_master",
        "sqlite_temp_master",
        "sqlite_schema",
        "sqlite_temp_schema",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat2",
        "sqlite_stat3",
        "sqlite_stat4",
    }
)

#: Physical tables + analysis views. Authorizer also sees view expansion.
APPROVED_RELATIONS: dict[str, frozenset[str]] = {}
for _table in SEMANTIC.tables:
    APPROVED_RELATIONS[_table.id] = frozenset(_table.columns)
for _view in SEMANTIC.analysis_views:
    APPROVED_RELATIONS[_view.id] = frozenset(_view.columns)

assert set(APPROVED_RELATIONS) == set(EXPECTED_TABLES) | set(EXPECTED_VIEWS)

APPROVED_RELATION_NAMES: frozenset[str] = frozenset(APPROVED_RELATIONS)

#: SQLite identifiers are compared case-insensitively. Validator, authorizer and
#: CTE scope checks all use this fold so ``Orders`` / ``ORDERS`` cannot slip past
#: a rule written against ``orders``.
APPROVED_RELATIONS_FOLD: dict[str, frozenset[str]] = {
    key.casefold(): frozenset(column.casefold() for column in columns)
    for key, columns in APPROVED_RELATIONS.items()
}
SYSTEM_TABLES_FOLD: frozenset[str] = frozenset(name.casefold() for name in SYSTEM_TABLES)

#: Functions that existing metrics, views or date grouping actually need.
#: Authorizer sees view-internal ``substr`` when querying v_revenue_lines.
APPROVED_FUNCTIONS: frozenset[str] = frozenset({"coalesce", "sum", "count", "substr"})


def fold_ident(name: str) -> str:
    """Normalize a SQLite identifier for allow-list and scope comparison."""
    # SQLite folds ASCII only; Unicode casefold would equate e.g. ſ and s.
    return name.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def is_system_table(name: str) -> bool:
    folded = fold_ident(name)
    return folded in SYSTEM_TABLES_FOLD or folded.startswith("sqlite_")


def approved_columns(relation: str) -> frozenset[str] | None:
    """Approved columns for a physical table or view, or None if unknown."""
    return APPROVED_RELATIONS_FOLD.get(fold_ident(relation))
