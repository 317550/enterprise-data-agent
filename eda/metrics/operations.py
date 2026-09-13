"""The closed set of computation operations.

The semantic YAML says *what* a metric is; this module is the only place that
says *how* to turn it into SQL. There are exactly three aggregation kinds and
each one is a hand-written function -- there is no expression parser, no
template language and no ``eval``. Adding a fourth way to compute a number
requires a code change and a test, which is the point.

Identifiers reaching the SQL text are safe by construction:
  1. the semantic schema restricts column names to ``^[a-z][a-z0-9_]*$``;
  2. cross-validation checks each one against the declared columns of the base
     view; and
  3. ``tests/test_semantic_consistency.py`` checks those declared columns
     against the real SQLite views.
"""

from __future__ import annotations

from collections.abc import Callable

from eda.semantic.models import (
    CountDistinctAggregation,
    MetricSpec,
    RatioAggregation,
    SumAggregation,
)

#: Analysis operations that are actually implemented right now. The semantic
#: config may legitimately declare more (they are business statements about what
#: the metric means); everything not listed here is 待实现.
IMPLEMENTED_ANALYSIS_OPERATIONS: frozenset[str] = frozenset({"total", "breakdown"})

#: Aggregation kinds this module can render.
IMPLEMENTED_AGGREGATIONS: frozenset[str] = frozenset({"sum", "count_distinct", "ratio"})


class UnsupportedOperationError(ValueError):
    """Raised when a metric asks for a computation this code does not implement."""


def _render_sum(field: str) -> str:
    # COALESCE so that an empty filter window yields 0 rather than NULL.
    return f"COALESCE(SUM({field}), 0)"


def _render_count_distinct(field: str) -> str:
    return f"COUNT(DISTINCT {field})"


def _render_ratio(numerator_sql: str, denominator_sql: str) -> str:
    # The zero denominator is turned into NULL here as well as in Python, so a
    # breakdown row can never show a divide-by-zero or a misleading 0.
    return (
        f"CASE WHEN {denominator_sql} = 0 THEN NULL "
        f"ELSE 1.0 * {numerator_sql} / {denominator_sql} END"
    )


def render_metric_sql(spec: MetricSpec, resolve: Callable[[str], MetricSpec]) -> str:
    """Render the scalar SQL expression for one metric.

    ``resolve`` maps a metric id to its spec; it is needed for ratio metrics,
    whose numerator and denominator are themselves metrics. Recursion depth is
    bounded because the semantic model forbids a ratio referring to itself and
    requires numerator/denominator to be non-ratio aggregations.
    """
    aggregation = spec.aggregation
    if isinstance(aggregation, SumAggregation):
        return _render_sum(aggregation.input_fields[0])
    if isinstance(aggregation, CountDistinctAggregation):
        return _render_count_distinct(aggregation.input_fields[0])
    if isinstance(aggregation, RatioAggregation):
        numerator = resolve(aggregation.numerator_metric)
        denominator = resolve(aggregation.denominator_metric)
        for part in (numerator, denominator):
            if isinstance(part.aggregation, RatioAggregation):
                raise UnsupportedOperationError(
                    f"metric {spec.id!r}: a ratio of ratios is not implemented "
                    f"(offending part: {part.id!r})"
                )
        return _render_ratio(
            render_metric_sql(numerator, resolve), render_metric_sql(denominator, resolve)
        )
    raise UnsupportedOperationError(  # pragma: no cover -- closed union
        f"metric {spec.id!r}: unknown aggregation {aggregation!r}"
    )


def require_implemented_analysis_operation(spec: MetricSpec, operation: str) -> None:
    """Guard used before running an analysis operation on a metric."""
    if operation not in IMPLEMENTED_ANALYSIS_OPERATIONS:
        raise UnsupportedOperationError(
            f"analysis operation {operation!r} is not implemented yet; "
            f"implemented: {sorted(IMPLEMENTED_ANALYSIS_OPERATIONS)}"
        )
    if operation not in spec.supported_operations:
        raise UnsupportedOperationError(
            f"metric {spec.id!r} does not support {operation!r}; "
            f"supported: {sorted(spec.supported_operations)}"
        )
