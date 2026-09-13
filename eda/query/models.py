"""Structured analysis results returned to the CLI and tests."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Completeness(BaseModel):
    """Whether the rows may be treated as the full requested population."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_complete_population: bool
    ranked_top_n: int | None
    truncated: bool
    truncation_reason: str | None
    reaggregation_safe: bool
    note_zh: str


class ExecutionInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str
    status: str
    sql: str
    columns: tuple[str, ...]
    row_count: int
    elapsed_ms: float
    truncated: bool
    truncation_reason: str | None
    error_code: str | None = None
    error_message: str | None = None


class ResultRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension_value: str | None = None
    metric_value: int | float | None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str
    semantic_version: str
    metric_id: str
    metric_name_zh: str
    display_metric_name_zh: str
    unit: str
    unit_code: str
    operation: str
    dimension_id: str | None
    dimension_name_zh: str | None
    start_date: str
    end_date: str
    filters: tuple[dict[str, Any], ...]
    rows: tuple[ResultRow, ...]
    warnings: tuple[str, ...]
    completeness: Completeness
    execution: ExecutionInfo
