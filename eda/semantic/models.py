"""Pydantic schema for the semantic layer.

Everything here is declarative data. The aggregation kinds form a *closed*
discriminated union -- adding a new way to compute a number means writing Python
in :mod:`eda.metrics.operations`, not putting an expression in YAML. That is the
whole point: a config file can never smuggle in executable logic.

Cross-file consistency (a metric pointing at a view that does not exist, a
dimension pointing at a column the view does not have, a ratio metric whose
denominator is missing) is checked in :meth:`SemanticModel` validation, so a
typo fails at load time rather than producing a wrong number later.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The only schema version this code knows how to read. Bump together with the
#: YAML files and with the migration note in docs/progress.md.
SUPPORTED_SCHEMA_VERSION = "1.1.0"

#: Analysis operations that a metric can meaningfully support. Closed set.
#: Which of these are *implemented today* is declared in
#: eda.metrics.operations.IMPLEMENTED_ANALYSIS_OPERATIONS, not here.
AnalysisOperation = Literal["total", "breakdown", "time_series", "compare", "mom", "contribution"]

#: What may be done with a dimension. Closed set.
DimensionOperation = Literal["group_by", "filter"]
AovDisplayName = Literal["客单价", "订单平均贡献额"]

Grain = Literal["order_line", "order", "customer", "product", "derived"]
Cardinality = Literal["one_to_one", "many_to_one", "one_to_many"]
# Direction is always ``from_table LEFT JOIN to_table``. ``duplicates_left``
# means one input row from ``from_table`` can match multiple ``to_table`` rows
# and therefore appear multiple times in the join result. Merely projecting the
# same to-table attribute onto several already-existing from-table rows is not
# fan-out.
FanOut = Literal["none", "duplicates_left"]
ValueType = Literal["categorical", "date", "month", "text"]

_IDENTIFIER = r"^[a-z][a-z0-9_]*$"


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class _VersionedFile(_Spec):
    """Common head of every semantic YAML file."""

    schema_version: str
    notes_zh: str = ""

    @field_validator("schema_version")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported semantic schema_version {value!r}; "
                f"this build understands {SUPPORTED_SCHEMA_VERSION!r}"
            )
        return value


# --- aggregations ------------------------------------------------------------


#: Column names are pasted into SQL text, so they are restricted to plain
#: snake_case identifiers here and additionally checked against the declared
#: columns of the base view. Two independent gates, neither of which relies on
#: escaping.
ColumnName = Annotated[str, Field(pattern=_IDENTIFIER)]


class SumAggregation(_Spec):
    """Additive measure: ``COALESCE(SUM(field), 0)``."""

    operation: Literal["sum"]
    input_fields: tuple[ColumnName, ...] = Field(min_length=1, max_length=1)


class CountDistinctAggregation(_Spec):
    """De-duplicated count: ``COUNT(DISTINCT field)``."""

    operation: Literal["count_distinct"]
    input_fields: tuple[ColumnName, ...] = Field(min_length=1, max_length=1)


class RatioAggregation(_Spec):
    """Derived measure: one metric divided by another."""

    operation: Literal["ratio"]
    numerator_metric: str
    denominator_metric: str

    @model_validator(mode="after")
    def _not_self_referential(self) -> RatioAggregation:
        if self.numerator_metric == self.denominator_metric:
            raise ValueError("ratio numerator and denominator must differ")
        return self


Aggregation = Annotated[
    SumAggregation | CountDistinctAggregation | RatioAggregation,
    Field(discriminator="operation"),
]


class ZeroDenominatorPolicy(_Spec):
    """What to do when a ratio's denominator is zero."""

    strategy: Literal["not_applicable", "return_null_with_note"]
    note_zh: str = ""

    @model_validator(mode="after")
    def _note_required_when_used(self) -> ZeroDenominatorPolicy:
        if self.strategy == "return_null_with_note" and not self.note_zh:
            raise ValueError("strategy 'return_null_with_note' requires note_zh")
        if self.strategy == "not_applicable" and self.note_zh:
            raise ValueError("strategy 'not_applicable' must not carry a note")
        return self


# --- metrics -----------------------------------------------------------------


class MetricSpec(_Spec):
    """One metric definition (口径)."""

    id: str = Field(pattern=_IDENTIFIER)
    legacy_ids: tuple[str, ...] = ()
    name_zh: str = Field(min_length=1)
    synonyms: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(pattern=_IDENTIFIER)
    unit_zh: str = Field(min_length=1)
    base_view: str = Field(pattern=_IDENTIFIER)
    grain: Grain
    aggregation: Aggregation
    additive: bool
    additive_dimensions: tuple[str, ...]
    contribution_dimensions: tuple[str, ...]
    supported_operations: tuple[AnalysisOperation, ...] = Field(min_length=1)
    status_include: tuple[str, ...]
    status_exclude: tuple[str, ...]
    filter_policy_zh: str = Field(min_length=1)
    date_field: str = Field(pattern=_IDENTIFIER)
    date_range_inclusive: Literal["both"]
    allowed_dimensions: tuple[str, ...]
    zero_denominator_policy: ZeroDenominatorPolicy
    definition_zh: str = Field(min_length=1)
    caveats_zh: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _statuses_disjoint(self) -> MetricSpec:
        overlap = set(self.status_include) & set(self.status_exclude)
        if overlap:
            raise ValueError(f"status_include and status_exclude overlap: {sorted(overlap)}")
        if not self.status_include:
            raise ValueError("status_include must not be empty")
        return self

    @model_validator(mode="after")
    def _ratio_needs_a_zero_policy(self) -> MetricSpec:
        is_ratio = isinstance(self.aggregation, RatioAggregation)
        has_policy = self.zero_denominator_policy.strategy == "return_null_with_note"
        if is_ratio and not has_policy:
            raise ValueError(f"ratio metric {self.id!r} must declare a zero denominator policy")
        if not is_ratio and has_policy:
            raise ValueError(
                f"metric {self.id!r} is not a ratio, so it must not declare "
                "a zero denominator policy"
            )
        return self

    @model_validator(mode="after")
    def _duplicate_dimensions(self) -> MetricSpec:
        if len(set(self.allowed_dimensions)) != len(self.allowed_dimensions):
            raise ValueError(f"metric {self.id!r} lists a dimension twice")
        return self

    @model_validator(mode="after")
    def _contribution_rules(self) -> MetricSpec:
        for dimensions in (self.additive_dimensions, self.contribution_dimensions):
            if len(set(dimensions)) != len(dimensions):
                raise ValueError("duplicate additivity/contribution dimension")
            if not set(dimensions) <= set(self.allowed_dimensions):
                raise ValueError("additivity/contribution dimension must be allowed")
        if not set(self.contribution_dimensions) <= set(self.additive_dimensions):
            raise ValueError("contribution requires additive dimensions")
        if bool(self.contribution_dimensions) != ("contribution" in self.supported_operations):
            raise ValueError("contribution capability and dimensions must agree")
        return self

    @property
    def all_ids(self) -> tuple[str, ...]:
        return (self.id, *self.legacy_ids)


class MetricsFile(_VersionedFile):
    metrics: tuple[MetricSpec, ...] = Field(min_length=1)


# --- dimensions --------------------------------------------------------------


class DimensionSpec(_Spec):
    """One dimension definition."""

    id: str = Field(pattern=_IDENTIFIER)
    name_zh: str = Field(min_length=1)
    synonyms: tuple[str, ...] = Field(min_length=1)
    source_view: str = Field(pattern=_IDENTIFIER)
    source_field: str = Field(pattern=_IDENTIFIER)
    value_type: ValueType
    value_format: str | None = None
    allowed_values: tuple[str, ...] | None = None
    allowed_operations: tuple[DimensionOperation, ...] = Field(min_length=1)
    #: Label used for ``aov_cents`` when this dimension is grouped. This is a
    #: closed business vocabulary, not a free expression. Category/product
    #: groupings divide line-level amount by related distinct orders, so they
    #: must say “订单平均贡献额”; dimensions that partition whole orders use
    #: the ordinary “客单价”.
    aov_display_name_zh: AovDisplayName
    forbidden_source_fields: tuple[str, ...] = ()
    #: Metrics that must NOT be summed across the values of this dimension,
    #: because the dimension does not partition them (a single order can appear
    #: under several categories). Empty means every metric is additive here.
    non_additive_metrics: tuple[str, ...] = ()
    non_additive_note_zh: str = ""
    notes_zh: str = Field(min_length=1)

    @model_validator(mode="after")
    def _non_additive_declaration_is_complete(self) -> DimensionSpec:
        if bool(self.non_additive_metrics) != bool(self.non_additive_note_zh):
            raise ValueError(
                f"dimension {self.id!r}: non_additive_metrics and non_additive_note_zh "
                "must either both be present or both be absent"
            )
        if len(set(self.non_additive_metrics)) != len(self.non_additive_metrics):
            raise ValueError(f"dimension {self.id!r}: duplicate non_additive_metrics")
        return self

    @model_validator(mode="after")
    def _consistent(self) -> DimensionSpec:
        if self.allowed_values is not None:
            if not self.allowed_values:
                raise ValueError(f"dimension {self.id!r}: allowed_values must not be empty")
            if len(set(self.allowed_values)) != len(self.allowed_values):
                raise ValueError(f"dimension {self.id!r}: duplicate allowed_values")
        if self.source_field in self.forbidden_source_fields:
            raise ValueError(
                f"dimension {self.id!r} uses {self.source_field!r}, "
                "which it also declares forbidden"
            )
        if len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError(f"dimension {self.id!r}: duplicate allowed_operations")
        return self

    def supports(self, operation: str) -> bool:
        return operation in self.allowed_operations


class DimensionsFile(_VersionedFile):
    dimensions: tuple[DimensionSpec, ...] = Field(min_length=1)


# --- relationships -----------------------------------------------------------


class TableSpec(_Spec):
    id: str = Field(pattern=_IDENTIFIER)
    name_zh: str = Field(min_length=1)
    grain_zh: str = Field(min_length=1)
    primary_key: tuple[ColumnName, ...] = Field(min_length=1)
    columns: tuple[ColumnName, ...] = Field(min_length=1)
    notes_zh: str = Field(min_length=1)

    @model_validator(mode="after")
    def _pk_is_a_column(self) -> TableSpec:
        missing = set(self.primary_key) - set(self.columns)
        if missing:
            raise ValueError(f"table {self.id!r}: primary key {sorted(missing)} not in columns")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"table {self.id!r}: duplicate column name")
        return self


class RelationshipSpec(_Spec):
    """A directed relationship from ``from_table`` to ``to_table``.

    ``cardinality`` and ``fan_out`` use that direction only:

    * ``many_to_one`` / ``one_to_one`` -> ``fan_out="none"``;
    * ``one_to_many`` -> ``fan_out="duplicates_left"``.

    Reverse-grain repetition is a separate concern. For example,
    ``order_items -> orders`` is many-to-one and does not multiply the
    ``order_items`` input rows, even though one order-level value is projected
    onto several existing item rows.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name_zh: str = Field(min_length=1)
    from_table: str = Field(pattern=_IDENTIFIER)
    from_columns: tuple[ColumnName, ...] = Field(min_length=1)
    to_table: str = Field(pattern=_IDENTIFIER)
    to_columns: tuple[ColumnName, ...] = Field(min_length=1)
    cardinality: Cardinality
    fan_out: FanOut
    double_count_note_zh: str = Field(min_length=1)

    @model_validator(mode="after")
    def _key_widths_match(self) -> RelationshipSpec:
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError(
                f"relationship {self.id!r}: join key width mismatch "
                f"({len(self.from_columns)} vs {len(self.to_columns)})"
            )
        if self.from_table == self.to_table:
            raise ValueError(f"relationship {self.id!r}: self-joins are not modelled")
        return self

    @model_validator(mode="after")
    def _fan_out_matches_forward_cardinality(self) -> RelationshipSpec:
        """Keep fan-out consistent with ``from_table -> to_table`` cardinality."""
        expected: FanOut = (
            "duplicates_left" if self.cardinality == "one_to_many" else "none"
        )
        if self.fan_out != expected:
            raise ValueError(
                f"relationship {self.id!r}: {self.cardinality} in the "
                "from_table -> to_table direction requires "
                f"fan_out={expected!r}, got {self.fan_out!r}"
            )
        return self


class AnalysisViewSpec(_Spec):
    """A pre-joined view: the safe join path, declared so it can be checked."""

    id: str = Field(pattern=_IDENTIFIER)
    name_zh: str = Field(min_length=1)
    base_grain: Grain
    source_tables: tuple[str, ...] = Field(min_length=1)
    status_filter: tuple[str, ...]
    columns: tuple[ColumnName, ...] = Field(min_length=1)
    additive_columns: tuple[ColumnName, ...]
    distinct_count_columns: tuple[ColumnName, ...]
    usage_notes_zh: str = Field(min_length=1)

    @model_validator(mode="after")
    def _columns_consistent(self) -> AnalysisViewSpec:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"view {self.id!r}: duplicate column name")
        for label, group in (
            ("additive_columns", self.additive_columns),
            ("distinct_count_columns", self.distinct_count_columns),
        ):
            missing = set(group) - set(self.columns)
            if missing:
                raise ValueError(f"view {self.id!r}: {label} {sorted(missing)} not in columns")
        return self


class RelationshipsFile(_VersionedFile):
    tables: tuple[TableSpec, ...] = Field(min_length=1)
    relationships: tuple[RelationshipSpec, ...] = Field(min_length=1)
    analysis_views: tuple[AnalysisViewSpec, ...] = Field(min_length=1)


# --- the assembled model -----------------------------------------------------


def _duplicates(values: tuple[str, ...] | list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


class SemanticModel(BaseModel):
    """The three files, loaded together and cross-checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    metrics: tuple[MetricSpec, ...]
    dimensions: tuple[DimensionSpec, ...]
    tables: tuple[TableSpec, ...]
    relationships: tuple[RelationshipSpec, ...]
    analysis_views: tuple[AnalysisViewSpec, ...]

    # --- lookups ---

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(metric.id for metric in self.metrics)

    @property
    def dimension_ids(self) -> tuple[str, ...]:
        return tuple(dimension.id for dimension in self.dimensions)

    @property
    def view_ids(self) -> tuple[str, ...]:
        return tuple(view.id for view in self.analysis_views)

    @property
    def table_ids(self) -> tuple[str, ...]:
        return tuple(table.id for table in self.tables)

    def metric(self, name: str) -> MetricSpec:
        """Look a metric up by canonical id, legacy id or synonym."""
        for metric in self.metrics:
            if name == metric.id or name in metric.legacy_ids:
                return metric
        for metric in self.metrics:
            if name in metric.synonyms:
                return metric
        raise KeyError(
            f"unknown metric {name!r}; known metrics: {sorted(self.metric_ids)}"
        )

    def dimension(self, name: str) -> DimensionSpec:
        for dimension in self.dimensions:
            if name == dimension.id:
                return dimension
        for dimension in self.dimensions:
            if name in dimension.synonyms:
                return dimension
        raise KeyError(
            f"unknown dimension {name!r}; known dimensions: {sorted(self.dimension_ids)}"
        )

    def view(self, view_id: str) -> AnalysisViewSpec:
        for view in self.analysis_views:
            if view.id == view_id:
                return view
        raise KeyError(f"unknown analysis view {view_id!r}")

    def table(self, table_id: str) -> TableSpec:
        for table in self.tables:
            if table.id == table_id:
                return table
        raise KeyError(f"unknown table {table_id!r}")

    def dimensions_supporting(self, operation: str) -> tuple[DimensionSpec, ...]:
        return tuple(d for d in self.dimensions if d.supports(operation))

    def is_additive_over(self, metric_id: str, dimension_id: str) -> bool:
        """One independent partition; never combine multiple contribution views."""
        metric = self.metric(metric_id)
        dimension = self.dimension(dimension_id)
        return (dimension.id in metric.additive_dimensions
                and metric.id not in dimension.non_additive_metrics)

    # --- cross-file validation ---

    @model_validator(mode="after")
    def _no_duplicate_ids(self) -> SemanticModel:
        for label, ids in (
            ("metric id", [m.id for m in self.metrics]),
            ("dimension id", [d.id for d in self.dimensions]),
            ("table id", [t.id for t in self.tables]),
            ("relationship id", [r.id for r in self.relationships]),
            ("analysis view id", [v.id for v in self.analysis_views]),
        ):
            dupes = _duplicates(ids)
            if dupes:
                raise ValueError(f"duplicate {label}: {dupes}")

        # A legacy id must not collide with any canonical id, and two metrics
        # must not claim the same legacy id or the same synonym.
        canonical = {m.id for m in self.metrics}
        legacy = [alias for m in self.metrics for alias in m.legacy_ids]
        clash = canonical & set(legacy)
        if clash:
            raise ValueError(f"legacy id also used as a canonical metric id: {sorted(clash)}")
        dupes = _duplicates(legacy)
        if dupes:
            raise ValueError(f"duplicate metric legacy id: {dupes}")

        names = [name for m in self.metrics for name in m.synonyms]
        dupes = _duplicates(names)
        if dupes:
            raise ValueError(f"the same synonym maps to two metrics: {dupes}")
        names = [name for d in self.dimensions for name in d.synonyms]
        dupes = _duplicates(names)
        if dupes:
            raise ValueError(f"the same synonym maps to two dimensions: {dupes}")
        return self

    @model_validator(mode="after")
    def _references_resolve(self) -> SemanticModel:
        view_columns = {view.id: set(view.columns) for view in self.analysis_views}
        table_ids = set(self.table_ids)
        dimension_ids = set(self.dimension_ids)
        metric_ids = set(self.metric_ids)

        for view in self.analysis_views:
            unknown = set(view.source_tables) - table_ids
            if unknown:
                raise ValueError(f"view {view.id!r} references unknown tables {sorted(unknown)}")

        for relationship in self.relationships:
            for side, table_id, columns in (
                ("from", relationship.from_table, relationship.from_columns),
                ("to", relationship.to_table, relationship.to_columns),
            ):
                if table_id not in table_ids:
                    raise ValueError(
                        f"relationship {relationship.id!r}: unknown {side}_table {table_id!r}"
                    )
                missing = set(columns) - set(self.table(table_id).columns)
                if missing:
                    raise ValueError(
                        f"relationship {relationship.id!r}: {side} join key "
                        f"{sorted(missing)} is not a column of {table_id!r}"
                    )
            target_pk = set(self.table(relationship.to_table).primary_key)
            if (
                relationship.cardinality == "many_to_one"
                and set(relationship.to_columns) != target_pk
            ):
                raise ValueError(
                    f"relationship {relationship.id!r} is many_to_one but does not join "
                    f"on the primary key of {relationship.to_table!r} "
                    f"({sorted(target_pk)})"
                )

        for metric in self.metrics:
            if metric.base_view not in view_columns:
                raise ValueError(
                    f"metric {metric.id!r} references unknown base_view {metric.base_view!r}"
                )
            columns = view_columns[metric.base_view]
            if metric.date_field not in columns:
                raise ValueError(
                    f"metric {metric.id!r}: date_field {metric.date_field!r} is not a "
                    f"column of {metric.base_view!r}"
                )
            aggregation = metric.aggregation
            if isinstance(aggregation, RatioAggregation):
                for role, referenced in (
                    ("numerator", aggregation.numerator_metric),
                    ("denominator", aggregation.denominator_metric),
                ):
                    if referenced not in metric_ids:
                        raise ValueError(
                            f"metric {metric.id!r}: {role}_metric {referenced!r} does not exist"
                        )
                    if referenced == metric.id:
                        raise ValueError(f"metric {metric.id!r}: ratio refers to itself")
            else:
                missing = set(aggregation.input_fields) - columns
                if missing:
                    raise ValueError(
                        f"metric {metric.id!r}: input_fields {sorted(missing)} are not "
                        f"columns of {metric.base_view!r}"
                    )
            unknown = set(metric.allowed_dimensions) - dimension_ids
            if unknown:
                raise ValueError(
                    f"metric {metric.id!r} allows unknown dimensions {sorted(unknown)}"
                )
            for dimension_id in metric.additive_dimensions:
                if metric.id in self.dimension(dimension_id).non_additive_metrics:
                    raise ValueError("conflicting dimension additivity declarations")
            for dimension_id in metric.contribution_dimensions:
                if not self.dimension(dimension_id).supports("group_by"):
                    raise ValueError("contribution dimension must support group_by")

        for dimension in self.dimensions:
            if dimension.source_view not in view_columns:
                raise ValueError(
                    f"dimension {dimension.id!r} references unknown view "
                    f"{dimension.source_view!r}"
                )
            if dimension.source_field not in view_columns[dimension.source_view]:
                raise ValueError(
                    f"dimension {dimension.id!r}: source_field {dimension.source_field!r} "
                    f"is not a column of {dimension.source_view!r}"
                )
            unknown = set(dimension.non_additive_metrics) - metric_ids
            if unknown:
                raise ValueError(
                    f"dimension {dimension.id!r}: non_additive_metrics {sorted(unknown)} "
                    "are not known metrics"
                )
        return self

    @model_validator(mode="after")
    def _ratio_base_views_agree(self) -> SemanticModel:
        """A ratio must be built from metrics on the same base, or it is nonsense."""
        for metric in self.metrics:
            if not isinstance(metric.aggregation, RatioAggregation):
                continue
            numerator = self.metric(metric.aggregation.numerator_metric)
            denominator = self.metric(metric.aggregation.denominator_metric)
            bases = {numerator.base_view, denominator.base_view, metric.base_view}
            if len(bases) != 1:
                raise ValueError(
                    f"ratio metric {metric.id!r} mixes base views {sorted(bases)}; "
                    "numerator and denominator must share one base view"
                )
            if numerator.status_include != denominator.status_include:
                raise ValueError(
                    f"ratio metric {metric.id!r}: numerator and denominator disagree "
                    "about which order statuses count"
                )
        return self
