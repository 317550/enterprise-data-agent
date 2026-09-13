"""Versioned semantic layer: the authoritative source of business definitions.

The YAML files under ``semantic/`` declare *what* a metric means; the Python in
:mod:`eda.metrics.operations` implements a small closed set of *how* to compute
it. There is deliberately no expression evaluator: no ``eval``, no ``exec``, no
user-supplied configuration.
"""

from eda.semantic.loader import (
    SEMANTIC_DIR,
    SemanticConfigError,
    load_semantic_model,
    load_semantic_model_from_dir,
)
from eda.semantic.models import (
    SUPPORTED_SCHEMA_VERSION,
    AnalysisViewSpec,
    CountDistinctAggregation,
    DimensionSpec,
    MetricSpec,
    RatioAggregation,
    RelationshipSpec,
    SemanticModel,
    SumAggregation,
    TableSpec,
    ZeroDenominatorPolicy,
)

__all__ = [
    "SEMANTIC_DIR",
    "SUPPORTED_SCHEMA_VERSION",
    "AnalysisViewSpec",
    "CountDistinctAggregation",
    "DimensionSpec",
    "MetricSpec",
    "RatioAggregation",
    "RelationshipSpec",
    "SemanticConfigError",
    "SemanticModel",
    "SumAggregation",
    "TableSpec",
    "ZeroDenominatorPolicy",
    "load_semantic_model",
    "load_semantic_model_from_dir",
]
