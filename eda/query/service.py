"""Run one AnalysisPlan through compile → validate → execute."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eda.metrics.definitions import AOV_METRIC_ID, METRIC_REGISTRY, SEMANTIC
from eda.plan.models import AnalysisPlan, parse_analysis_plan
from eda.query.errors import QueryError
from eda.query.models import AnalysisResult, Completeness, ExecutionInfo, ResultRow
from eda.sql.compiler import compile_plan
from eda.sql.executor import ExecutionLimits, execute_readonly_query, require_ok


def run_analysis_plan(
    payload: dict[str, Any] | AnalysisPlan,
    db_path: str | Path,
    limits: ExecutionLimits | None = None,
) -> AnalysisResult:
    plan = parse_analysis_plan(payload)
    compiled = compile_plan(plan)
    raw = execute_readonly_query(db_path, compiled.sql, compiled.params, limits=limits)
    execution = ExecutionInfo(
        query_id=raw.query_id,
        status=raw.status,
        sql=raw.sql,
        columns=raw.columns,
        row_count=raw.row_count,
        elapsed_ms=raw.elapsed_ms,
        truncated=raw.truncated,
        truncation_reason=raw.truncation_reason,
        error_code=raw.error_code,
        error_message=raw.error_message,
    )
    if raw.status != "ok":
        raise QueryError(raw.error_code or "db_error", raw.error_message or "query failed")
    require_ok(raw)

    definition = METRIC_REGISTRY[plan.metric_id]
    dimension = SEMANTIC.dimension(plan.dimension_id) if plan.dimension_id else None
    rows = _rows_from_execution(plan.operation, raw.columns, raw.rows)
    warnings = _warnings(plan, definition, dimension, rows)
    ranked = plan.top_n if plan.operation == "breakdown" else None
    is_complete = ranked is None and not raw.truncated
    reaggregation_safe = is_complete and definition.additive
    note = _completeness_note(is_complete, ranked, raw.truncated, raw.truncation_reason)
    return AnalysisResult(
        query_id=raw.query_id,
        semantic_version=compiled.semantic_version,
        metric_id=plan.metric_id,
        metric_name_zh=definition.name_zh,
        display_metric_name_zh=compiled.display_metric_name_zh,
        unit=definition.unit,
        unit_code=definition.unit_code,
        operation=plan.operation,
        dimension_id=plan.dimension_id,
        dimension_name_zh=None if dimension is None else dimension.name_zh,
        start_date=plan.start_date,
        end_date=plan.end_date,
        filters=tuple(clause.model_dump(exclude={"value"}) for clause in plan.filters),
        rows=rows,
        warnings=warnings,
        completeness=Completeness(
            is_complete_population=is_complete,
            ranked_top_n=ranked,
            truncated=raw.truncated,
            truncation_reason=raw.truncation_reason,
            reaggregation_safe=reaggregation_safe,
            note_zh=note,
        ),
        execution=execution,
    )


def _rows_from_execution(
    operation: str,
    columns: tuple[str, ...],
    raw_rows: tuple[tuple[object, ...], ...],
) -> tuple[ResultRow, ...]:
    index = {name: i for i, name in enumerate(columns)}
    if "metric_value" not in index:
        raise QueryError("db_error", "query result is missing metric_value")
    rows: list[ResultRow] = []
    for raw in raw_rows:
        metric = _metric_number(raw[index["metric_value"]])
        dimension_value = None
        if operation == "breakdown":
            if "dimension_value" not in index:
                raise QueryError("db_error", "breakdown result is missing dimension_value")
            value = raw[index["dimension_value"]]
            dimension_value = None if value is None else str(value)
        rows.append(ResultRow(dimension_value=dimension_value, metric_value=metric))
    return tuple(rows)


def _metric_number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise QueryError("db_error", "unexpected boolean metric value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    raise QueryError("db_error", "unexpected metric value type")


def _warnings(plan, definition, dimension, rows: tuple[ResultRow, ...]) -> tuple[str, ...]:
    notes: list[str] = []
    if definition.key == AOV_METRIC_ID and any(row.metric_value is None for row in rows):
        note = METRIC_REGISTRY[AOV_METRIC_ID].zero_denominator_note
        if note:
            notes.append(note)
    if (
        plan.operation == "breakdown"
        and dimension is not None
        and definition.key in dimension.non_additive_metrics
        and dimension.non_additive_note_zh
    ):
        notes.append(dimension.non_additive_note_zh)
    if plan.operation == "breakdown" and not rows:
        notes.append(
            f"{plan.start_date} 至 {plan.end_date}（含端点）下没有 "
            f"{'/'.join(definition.status_include)} 订单明细，拆分结果为空。"
        )
    if plan.operation == "total" and not rows:
        notes.append("查询成功但没有返回聚合行。")
    return tuple(notes)


def _completeness_note(
    is_complete: bool,
    ranked: int | None,
    truncated: bool,
    reason: str | None,
) -> str:
    parts: list[str] = []
    if ranked is not None:
        parts.append(
            f"这是按请求排序后的前 {ranked} 项，不是全部分组，不能据此重新求总体。"
        )
    if truncated:
        label = "行数上限" if reason == "max_rows" else "返回字节上限"
        parts.append(f"执行器因{label}截断了结果（truncated=true），不能当作完整总体。")
    if is_complete:
        parts.append("结果覆盖本次筛选下的完整总体（未做 top_n，也未被执行器截断）。")
    return "".join(parts)
