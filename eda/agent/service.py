"""Bounded planning orchestration. Only the stage-two service executes data queries."""

import logging
import uuid
from pathlib import Path

from eda.agent.context import build_context
from eda.agent.dates import resolve_dates, strict_date
from eda.agent.models import (
    AgentResult, DATE_QUESTION, DecisionError, INVALID_DATE_QUESTION,
    MAX_MODEL_CALLS, parse_decision,
)
from eda.agent.planner import ModelFailure, PlannerModel, preflight_refusal
from eda.config import get_settings
from eda.metrics.definitions import SEMANTIC
from eda.query.errors import QueryError
from eda.query.service import run_analysis_plan
from eda.sql.executor import ExecutionLimits

logger = logging.getLogger(__name__)
SAFE_ERRORS = {
    "invalid_input": "问题或参考日期无效。",
    "model_timeout": "规划服务请求超时。",
    "model_unavailable": "规划服务暂时不可用。",
    "model_configuration_error": "真实规划服务未正确配置。",
    "plan_generation_failed": "规划输出未通过校验，已停止。",
    "execution_failed": "查询未能安全完成，请检查查询范围或本地配置。",
}


def run_question(question: str, db_path: str | Path, *, model: PlannerModel,
                 reference_date: str | None = None, limits: ExecutionLimits | None = None) -> AgentResult:
    reference = reference_date if reference_date is not None else get_settings().analysis_reference_date
    metadata = {"request_id": uuid.uuid4().hex, "semantic_version": SEMANTIC.schema_version,
                "model_name": model.model_name, "reference_date": reference,
                "model_call_count": 0, "validation_errors": ()}

    def finish(status, **payload):
        result = AgentResult(**metadata, status=status, **payload)
        logger.info("planning_request %s", result.audit_record().model_dump_json(exclude_none=True))
        return result

    try:
        strict_date(reference)
        if not isinstance(question, str) or not question.strip() or len(question) > 2048:
            raise ValueError("invalid question")
    except (ValueError, TypeError):
        metadata["reference_date"] = "invalid"
        return finish("plan_validation_error", error_code="invalid_input", error_message=SAFE_ERRORS["invalid_input"])
    blocked = preflight_refusal(question)
    if blocked:
        return finish("refused", decision_status="refuse", refusal_category=blocked.refusal_category,
                      refusal_reason=blocked.refusal_reason)
    window = resolve_dates(question, reference)
    if window.issue:
        return finish("clarification_required", decision_status="clarify",
                      clarification=DATE_QUESTION if window.issue == "missing_date" else INVALID_DATE_QUESTION)

    repair_error = None
    for attempt in range(MAX_MODEL_CALLS):
        context = build_context(reference, repair_error=repair_error)
        metadata["model_call_count"] += 1
        try:
            raw = model.plan(question, context)
        except (TimeoutError, ModelFailure) as exc:
            code = "model_timeout" if isinstance(exc, TimeoutError) else exc.code
            return finish("model_error", error_code=code, error_message=SAFE_ERRORS[code])
        except Exception:
            return finish("model_error", error_code="model_unavailable", error_message=SAFE_ERRORS["model_unavailable"])
        try:
            decision = parse_decision(raw)
            if decision.status == "ready" and (decision.plan.start_date, decision.plan.end_date) != (window.start_date, window.end_date):
                raise DecisionError("date_mismatch")
        except DecisionError as exc:
            repair_error = exc.code
            metadata["validation_errors"] += (exc.code,)
            if attempt + 1 == MAX_MODEL_CALLS:
                return finish("plan_validation_error", error_code="plan_generation_failed",
                              error_message=SAFE_ERRORS["plan_generation_failed"])
            continue
        if decision.status == "clarify":
            return finish("clarification_required", decision_status="clarify", clarification=decision.clarification)
        if decision.status == "refuse":
            return finish("refused", decision_status="refuse", refusal_category=decision.refusal_category,
                          refusal_reason=decision.refusal_reason)
        try:
            analysis = run_analysis_plan(decision.plan, db_path, limits=limits)
        except QueryError as exc:
            # Execution outcomes are terminal: never feed SQL/errors back to a model.
            return finish("execution_error", decision_status="ready", error_code=exc.code,
                          error_message=SAFE_ERRORS["execution_failed"])
        except (OSError, ValueError):
            return finish("execution_error", decision_status="ready", error_code="db_error",
                          error_message=SAFE_ERRORS["execution_failed"])
        warnings = window.warnings + analysis.warnings
        if not analysis.completeness.is_complete_population:
            warnings += (analysis.completeness.note_zh,)
        if not analysis.rows:
            summary = "查询成功，没有匹配的分组数据。"
        elif not analysis.completeness.is_complete_population:
            summary = "查询成功，结果为排名子集或执行器截断结果，不能据此汇总总体。"
        else:
            summary = "查询成功，结果覆盖本次计划请求的范围；数据期间完整性未作推断。"
        return finish("success", decision_status="ready", plan=decision.plan, analysis_result=analysis,
                      start_date=analysis.start_date, end_date=analysis.end_date, query_id=analysis.query_id,
                      warnings=warnings, summary=summary)
    raise AssertionError("bounded planner must return an outcome")
