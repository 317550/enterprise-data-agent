"""Loading and validating the semantic layer.

The point of these tests is that a *typo* in ``semantic/*.yaml`` is a loud
failure at load time, not a wrong number three stages later. Each broken-config
case starts from the real configuration and mutates exactly one thing, so the
test proves that particular rule is what rejects it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from eda.metrics.operations import (
    IMPLEMENTED_AGGREGATIONS,
    IMPLEMENTED_ANALYSIS_OPERATIONS,
)
from eda.semantic import (
    SEMANTIC_DIR,
    SUPPORTED_SCHEMA_VERSION,
    RelationshipSpec,
    SemanticConfigError,
    load_semantic_model,
    load_semantic_model_from_dir,
)
from eda.semantic.loader import (
    DIMENSIONS_FILENAME,
    METRICS_FILENAME,
    RELATIONSHIPS_FILENAME,
    REQUIRED_FILENAMES,
)

_FILE_KEYS = {
    METRICS_FILENAME: "metrics",
    DIMENSIONS_FILENAME: "dimensions",
    RELATIONSHIPS_FILENAME: "tables",
}


@pytest.fixture(scope="session")
def raw_config() -> dict[str, dict[str, Any]]:
    """The real YAML, parsed but not validated."""
    return {
        name: yaml.safe_load((SEMANTIC_DIR / name).read_text(encoding="utf-8"))
        for name in REQUIRED_FILENAMES
    }


def _write(directory: Path, raw: dict[str, dict[str, Any]]) -> Path:
    for name, payload in raw.items():
        (directory / name).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return directory


@pytest.fixture
def mutable_config(
    tmp_path: Path, raw_config: dict[str, dict[str, Any]]
) -> tuple[Path, dict[str, dict[str, Any]]]:
    """A writable copy of the real config, ready to be broken on purpose."""
    raw = copy.deepcopy(raw_config)
    return tmp_path, raw


# --- the real configuration --------------------------------------------------


def test_real_config_loads() -> None:
    model = load_semantic_model()
    assert model.schema_version == SUPPORTED_SCHEMA_VERSION
    assert model.metrics and model.dimensions and model.tables
    assert model.relationships and model.analysis_views


def test_loading_is_cached() -> None:
    assert load_semantic_model() is load_semantic_model()


def test_round_trip_through_a_tmp_dir_is_equivalent(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    """The tmp-dir harness must reproduce the real model, or the negative
    tests below would be testing the harness rather than the rules."""
    directory, raw = mutable_config
    assert load_semantic_model_from_dir(_write(directory, raw)) == load_semantic_model()


def test_every_metric_declares_the_required_fields() -> None:
    model = load_semantic_model()
    for metric in model.metrics:
        assert metric.id and metric.name_zh and metric.synonyms
        assert metric.unit and metric.unit_zh
        assert metric.base_view and metric.grain
        assert metric.supported_operations
        assert metric.aggregation.operation in IMPLEMENTED_AGGREGATIONS
        assert metric.status_include
        assert metric.filter_policy_zh
        assert metric.allowed_dimensions
        assert metric.zero_denominator_policy.strategy
        assert metric.date_range_inclusive == "both"


def test_every_dimension_declares_the_required_fields() -> None:
    model = load_semantic_model()
    for dimension in model.dimensions:
        assert dimension.id and dimension.name_zh and dimension.synonyms
        assert dimension.source_view and dimension.source_field
        assert dimension.value_type
        assert dimension.allowed_operations
        assert dimension.aov_display_name_zh in {"客单价", "订单平均贡献额"}
        assert dimension.notes_zh


def test_region_dimension_uses_order_time_region_not_signup_region() -> None:
    region = load_semantic_model().dimension("region")
    assert region.source_field == "order_region"
    assert "signup_region" in region.forbidden_source_fields


def test_no_dimension_sources_a_customer_registration_field() -> None:
    """Registration attributes must never be used as the order-time dimension."""
    forbidden = {"signup_region", "signup_channel", "signup_date"}
    offenders = [
        dimension.id
        for dimension in load_semantic_model().dimensions
        if dimension.source_field in forbidden
    ]
    assert offenders == []


def test_relationships_cover_the_existing_tables_only() -> None:
    model = load_semantic_model()
    assert set(model.table_ids) == {"customers", "products", "orders", "order_items"}
    pairs = {(r.from_table, r.to_table) for r in model.relationships}
    assert pairs == {
        ("orders", "customers"),
        ("order_items", "orders"),
        ("order_items", "products"),
    }


def test_all_existing_relationships_follow_the_forward_fan_out_rule() -> None:
    """All current relationships are many-to-one in their declared direction."""
    model = load_semantic_model()
    assert {r.cardinality for r in model.relationships} == {"many_to_one"}
    assert {r.fan_out for r in model.relationships} == {"none"}

    note = model_relationship_note(model, "order_items__orders")
    assert "不会复制左侧 order_items 输入行" in note
    assert "不是正向连接对左侧行的放大" in note
    assert "COUNT(DISTINCT order_id)" in note
    assert "不能直接 SUM" in note


def model_relationship_note(model, relationship_id: str) -> str:
    for relationship in model.relationships:
        if relationship.id == relationship_id:
            return relationship.double_count_note_zh
    raise AssertionError(f"relationship {relationship_id!r} missing")


def _relationship(*, cardinality: str, fan_out: str) -> RelationshipSpec:
    return RelationshipSpec(
        id="left__right",
        name_zh="测试关系",
        from_table="left_table",
        from_columns=("foreign_key",),
        to_table="right_table",
        to_columns=("primary_key",),
        cardinality=cardinality,
        fan_out=fan_out,
        double_count_note_zh="仅用于验证正向 fan_out 规则。",
    )


@pytest.mark.parametrize("cardinality", ["many_to_one", "one_to_one"])
def test_non_expanding_forward_cardinalities_require_fan_out_none(
    cardinality: str,
) -> None:
    assert _relationship(cardinality=cardinality, fan_out="none").fan_out == "none"
    with pytest.raises(ValueError, match="requires fan_out='none'"):
        _relationship(cardinality=cardinality, fan_out="duplicates_left")


def test_one_to_many_forward_cardinality_requires_duplicates_left() -> None:
    relationship = _relationship(
        cardinality="one_to_many", fan_out="duplicates_left"
    )
    assert relationship.fan_out == "duplicates_left"

    with pytest.raises(ValueError, match="requires fan_out='duplicates_left'"):
        _relationship(cardinality="one_to_many", fan_out="none")


def test_implemented_operations_are_a_subset_of_what_the_config_supports() -> None:
    """Code may lag behind the config, but must never claim more than it does."""
    model = load_semantic_model()
    declared: set[str] = set()
    for metric in model.metrics:
        declared |= set(metric.supported_operations)
    assert IMPLEMENTED_ANALYSIS_OPERATIONS <= declared


def test_chinese_prose_has_no_yaml_fold_artifacts(
    raw_config: dict[str, dict[str, Any]],
) -> None:
    """A folded scalar spanning lines inserts a space; in Chinese that is a typo.

    Keeping this honest matters because these strings are shown to users.
    """
    offenders: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for artifact in ("， ", "。 ", "、 ", "； ", "： "):
                if artifact in node:
                    offenders.append(f"{path}: {artifact!r} in {node[:40]!r}")

    for name, payload in raw_config.items():
        walk(payload, name)
    assert offenders == [], f"YAML fold artifacts (write long prose on one line): {offenders}"


# --- missing / malformed files -----------------------------------------------


@pytest.mark.parametrize("missing", REQUIRED_FILENAMES)
def test_missing_file_is_reported(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]], missing: str
) -> None:
    directory, raw = mutable_config
    del raw[missing]
    with pytest.raises(SemanticConfigError, match="not found"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_empty_file_is_reported(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    _write(directory, raw)
    (directory / METRICS_FILENAME).write_text("", encoding="utf-8")
    with pytest.raises(SemanticConfigError, match="empty"):
        load_semantic_model_from_dir(directory)


def test_broken_yaml_is_reported(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    _write(directory, raw)
    (directory / DIMENSIONS_FILENAME).write_text("dimensions: [unclosed", encoding="utf-8")
    with pytest.raises(SemanticConfigError, match="not valid YAML"):
        load_semantic_model_from_dir(directory)


def test_top_level_must_be_a_mapping(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    _write(directory, raw)
    (directory / METRICS_FILENAME).write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SemanticConfigError, match="mapping"):
        load_semantic_model_from_dir(directory)


def test_yaml_cannot_construct_python_objects(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    """safe_load refuses !!python tags, so a config can never build an object."""
    directory, raw = mutable_config
    _write(directory, raw)
    (directory / METRICS_FILENAME).write_text(
        "schema_version: '1.0.0'\nmetrics: !!python/object/apply:os.system ['echo hi']\n",
        encoding="utf-8",
    )
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(directory)


# --- schema version ----------------------------------------------------------


@pytest.mark.parametrize("filename", REQUIRED_FILENAMES)
def test_unsupported_schema_version_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]], filename: str
) -> None:
    directory, raw = mutable_config
    raw[filename]["schema_version"] = "9.9.9"
    with pytest.raises(SemanticConfigError, match="schema_version"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_missing_schema_version_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    del raw[METRICS_FILENAME]["schema_version"]
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


# --- duplicate ids -----------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "key", "label"),
    [
        (METRICS_FILENAME, "metrics", "metric id"),
        (DIMENSIONS_FILENAME, "dimensions", "dimension id"),
        (RELATIONSHIPS_FILENAME, "tables", "table id"),
        (RELATIONSHIPS_FILENAME, "relationships", "relationship id"),
        (RELATIONSHIPS_FILENAME, "analysis_views", "analysis view id"),
    ],
)
def test_duplicate_ids_are_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
    filename: str,
    key: str,
    label: str,
) -> None:
    directory, raw = mutable_config
    entries = raw[filename][key]
    entries.append(copy.deepcopy(entries[0]))
    with pytest.raises(SemanticConfigError, match=f"duplicate {label}"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_legacy_id_colliding_with_a_canonical_id_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][1]["legacy_ids"] = ["aov_cents"]
    with pytest.raises(SemanticConfigError, match="legacy id"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_a_synonym_may_not_point_at_two_metrics(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    metrics = raw[METRICS_FILENAME]["metrics"]
    metrics[1]["synonyms"] = [*metrics[1]["synonyms"], metrics[0]["synonyms"][0]]
    with pytest.raises(SemanticConfigError, match="same synonym"):
        load_semantic_model_from_dir(_write(directory, raw))


# --- dangling references -----------------------------------------------------


def test_metric_on_an_unknown_base_view_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["base_view"] = "v_does_not_exist"
    with pytest.raises(SemanticConfigError, match="unknown base_view"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_metric_input_field_must_be_a_column_of_its_base_view(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["aggregation"]["input_fields"] = ["nope_cents"]
    with pytest.raises(SemanticConfigError, match="input_fields"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_metric_date_field_must_be_a_column_of_its_base_view(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["date_field"] = "not_a_date"
    with pytest.raises(SemanticConfigError, match="date_field"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_metric_allowing_an_unknown_dimension_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["allowed_dimensions"].append("weather")
    with pytest.raises(SemanticConfigError, match="unknown dimensions"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_ratio_with_a_missing_denominator_metric_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    for metric in raw[METRICS_FILENAME]["metrics"]:
        if metric["aggregation"]["operation"] == "ratio":
            metric["aggregation"]["denominator_metric"] = "ghost_metric"
    with pytest.raises(SemanticConfigError, match="denominator_metric"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_ratio_referring_to_itself_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    for metric in raw[METRICS_FILENAME]["metrics"]:
        if metric["aggregation"]["operation"] == "ratio":
            metric["aggregation"]["numerator_metric"] = metric["id"]
    with pytest.raises(SemanticConfigError, match="itself|refers to itself"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_dimension_on_an_unknown_column_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[DIMENSIONS_FILENAME]["dimensions"][0]["source_field"] = "phantom_column"
    with pytest.raises(SemanticConfigError, match="source_field"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_dimension_on_an_unknown_view_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[DIMENSIONS_FILENAME]["dimensions"][0]["source_view"] = "v_ghost"
    with pytest.raises(SemanticConfigError, match="unknown view"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_non_additive_metrics_must_exist(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    for dimension in raw[DIMENSIONS_FILENAME]["dimensions"]:
        if dimension["id"] == "category":
            dimension["non_additive_metrics"] = ["ghost_metric"]
    with pytest.raises(SemanticConfigError, match="non_additive_metrics"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_view_on_an_unknown_table_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[RELATIONSHIPS_FILENAME]["analysis_views"][0]["source_tables"] = ["shipments"]
    with pytest.raises(SemanticConfigError, match="unknown tables"):
        load_semantic_model_from_dir(_write(directory, raw))


# --- invalid join keys -------------------------------------------------------


def test_join_key_that_is_not_a_column_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[RELATIONSHIPS_FILENAME]["relationships"][0]["from_columns"] = ["not_a_column"]
    with pytest.raises(SemanticConfigError, match="join key"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_join_key_width_mismatch_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    relationship = raw[RELATIONSHIPS_FILENAME]["relationships"][0]
    relationship["from_columns"] = ["customer_id", "order_id"]
    with pytest.raises(SemanticConfigError, match="width mismatch"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_unknown_join_table_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[RELATIONSHIPS_FILENAME]["relationships"][0]["to_table"] = "warehouses"
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_many_to_one_must_join_on_the_target_primary_key(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    """Joining many_to_one on a non-key column would silently fan out rows."""
    directory, raw = mutable_config
    for relationship in raw[RELATIONSHIPS_FILENAME]["relationships"]:
        if relationship["id"] == "orders__customers":
            relationship["to_columns"] = ["signup_region"]
            relationship["from_columns"] = ["order_region"]
    with pytest.raises(SemanticConfigError, match="primary key"):
        load_semantic_model_from_dir(_write(directory, raw))


# --- invalid operations ------------------------------------------------------


def test_unknown_aggregation_operation_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["aggregation"] = {
        "operation": "median",
        "input_fields": ["line_amount_cents"],
    }
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_unknown_analysis_operation_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["supported_operations"] = ["forecast"]
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_unknown_dimension_operation_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[DIMENSIONS_FILENAME]["dimensions"][0]["allowed_operations"] = ["drill_down"]
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_arbitrary_aov_display_name_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    """The report label is a closed vocabulary, not arbitrary display input."""
    directory, raw = mutable_config
    raw[DIMENSIONS_FILENAME]["dimensions"][0]["aov_display_name_zh"] = "随意名称"
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_unknown_cardinality_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[RELATIONSHIPS_FILENAME]["relationships"][0]["cardinality"] = "sometimes"
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


def test_unexpected_field_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    """extra='forbid': a misspelt key is an error, not a silently ignored one."""
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["zero_denominater_policy"] = {"strategy": "oops"}
    with pytest.raises(SemanticConfigError):
        load_semantic_model_from_dir(_write(directory, raw))


# --- zero denominator policy -------------------------------------------------


def test_ratio_metric_without_a_zero_denominator_policy_is_rejected(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    for metric in raw[METRICS_FILENAME]["metrics"]:
        if metric["aggregation"]["operation"] == "ratio":
            metric["zero_denominator_policy"] = {"strategy": "not_applicable"}
    with pytest.raises(SemanticConfigError, match="zero denominator policy"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_zero_denominator_note_is_required_when_the_strategy_needs_one(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    for metric in raw[METRICS_FILENAME]["metrics"]:
        if metric["aggregation"]["operation"] == "ratio":
            metric["zero_denominator_policy"] = {"strategy": "return_null_with_note"}
    with pytest.raises(SemanticConfigError, match="note_zh"):
        load_semantic_model_from_dir(_write(directory, raw))


def test_statuses_may_not_be_both_included_and_excluded(
    mutable_config: tuple[Path, dict[str, dict[str, Any]]],
) -> None:
    directory, raw = mutable_config
    raw[METRICS_FILENAME]["metrics"][0]["status_exclude"].append("paid")
    with pytest.raises(SemanticConfigError, match="overlap"):
        load_semantic_model_from_dir(_write(directory, raw))


# --- trust boundary ----------------------------------------------------------


def test_no_cli_lets_a_user_point_at_another_semantic_directory() -> None:
    """The semantic layer is a controlled asset: no upload, no override flag."""
    from eda.data import build_db
    from eda.metrics import report

    for module in (report, build_db):
        actions = module.build_parser()._actions
        options = {option for action in actions for option in action.option_strings}
        assert not {option for option in options if "semantic" in option}, module.__name__


def test_runtime_code_never_writes_yaml_or_text_files() -> None:
    """Nothing under eda/ can rewrite the semantic config (or any text file).

    Broader than strictly necessary on purpose: the only writer in this project
    is the database builder, and it writes through sqlite3, not text I/O.
    """
    offenders = [
        path.relative_to(SEMANTIC_DIR.parent).as_posix()
        for path in (SEMANTIC_DIR.parent / "eda").rglob("*.py")
        if "write_text" in path.read_text(encoding="utf-8")
        or "yaml.safe_dump" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"runtime code must not write text/YAML files: {offenders}"
