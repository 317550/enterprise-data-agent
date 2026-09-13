"""Load and validate the semantic layer from ``semantic/*.yaml``.

Trust boundary
--------------
The semantic configuration is a *controlled asset*: it ships with the code and
is reviewed like code. There is no upload path, no environment variable and no
CLI flag that points this loader somewhere else -- the directory argument exists
only so that tests can feed it deliberately broken files from a tmp directory.
``tests/test_semantic_config.py`` asserts that no CLI exposes it.

YAML is read with ``yaml.safe_load``, which cannot construct arbitrary Python
objects, so a malformed file can only ever produce a validation error.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from eda.config import PROJECT_ROOT
from eda.semantic.models import (
    DimensionsFile,
    MetricsFile,
    RelationshipsFile,
    SemanticModel,
)

#: Where the controlled configuration lives.
SEMANTIC_DIR: Path = PROJECT_ROOT / "semantic"

METRICS_FILENAME = "metrics.yaml"
DIMENSIONS_FILENAME = "dimensions.yaml"
RELATIONSHIPS_FILENAME = "relationships.yaml"

REQUIRED_FILENAMES: tuple[str, ...] = (
    METRICS_FILENAME,
    DIMENSIONS_FILENAME,
    RELATIONSHIPS_FILENAME,
)


class SemanticConfigError(RuntimeError):
    """Raised when the semantic configuration is missing, malformed or inconsistent."""


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SemanticConfigError(f"semantic config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SemanticConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if raw is None:
        raise SemanticConfigError(f"{path.name} is empty")
    if not isinstance(raw, dict):
        raise SemanticConfigError(
            f"{path.name} must contain a mapping at the top level, got {type(raw).__name__}"
        )
    return raw


def load_semantic_model_from_dir(directory: Path) -> SemanticModel:
    """Load the three files from ``directory`` and cross-validate them.

    Every failure -- missing file, bad YAML, schema violation, dangling
    reference -- is reported as :class:`SemanticConfigError`.
    """
    directory = Path(directory)
    metrics_raw = _read_yaml_mapping(directory / METRICS_FILENAME)
    dimensions_raw = _read_yaml_mapping(directory / DIMENSIONS_FILENAME)
    relationships_raw = _read_yaml_mapping(directory / RELATIONSHIPS_FILENAME)

    try:
        metrics_file = MetricsFile(**metrics_raw)
        dimensions_file = DimensionsFile(**dimensions_raw)
        relationships_file = RelationshipsFile(**relationships_raw)
    except (TypeError, ValueError) as exc:
        raise SemanticConfigError(f"invalid semantic config in {directory}: {exc}") from exc

    versions = {
        METRICS_FILENAME: metrics_file.schema_version,
        DIMENSIONS_FILENAME: dimensions_file.schema_version,
        RELATIONSHIPS_FILENAME: relationships_file.schema_version,
    }
    if len(set(versions.values())) != 1:
        raise SemanticConfigError(f"semantic files declare different schema versions: {versions}")

    try:
        return SemanticModel(
            schema_version=metrics_file.schema_version,
            metrics=metrics_file.metrics,
            dimensions=dimensions_file.dimensions,
            tables=relationships_file.tables,
            relationships=relationships_file.relationships,
            analysis_views=relationships_file.analysis_views,
        )
    except (TypeError, ValueError) as exc:
        raise SemanticConfigError(f"inconsistent semantic config in {directory}: {exc}") from exc


@functools.lru_cache(maxsize=1)
def load_semantic_model() -> SemanticModel:
    """The project's semantic model, loaded once from :data:`SEMANTIC_DIR`."""
    return load_semantic_model_from_dir(SEMANTIC_DIR)
