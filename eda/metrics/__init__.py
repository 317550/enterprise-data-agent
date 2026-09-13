"""Metric definitions (口径) and their computation."""

from eda.metrics.core import CoreMetrics, compute_breakdown, compute_core_metrics
from eda.metrics.definitions import (
    BREAKDOWN_DIMENSIONS,
    METRIC_REGISTRY,
    MetricDefinition,
    MetricFilters,
    build_breakdown_sql,
    build_core_metrics_sql,
)

__all__ = [
    "CoreMetrics",
    "compute_breakdown",
    "compute_core_metrics",
    "BREAKDOWN_DIMENSIONS",
    "METRIC_REGISTRY",
    "MetricDefinition",
    "MetricFilters",
    "build_breakdown_sql",
    "build_core_metrics_sql",
]
