"""Strict business decisions and safe request results, without executable text."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda.plan.models import AnalysisPlan, parse_analysis_plan
from eda.query.models import AnalysisResult

PROMPT_VERSION = "nl-plan-v1"
MAX_MODEL_CALLS = 2
MAX_OUTPUT_CHARS = 16384

Clarification = Literal[
    "请明确要查询的一个指标，例如成交额、订单数或客单价。",
    "请提供明确的起止日期，或一个明确的年份、月份。",
    "日期不存在、格式不规范或范围不明确，请使用有效的 YYYY-MM-DD 起止日期。",
    "本次只支持一个拆分维度，请选择地区、类别、渠道、商品、月份或日期中的一个。",
    "请明确筛选条件；本次只支持语义层批准的地区和类别取值。",
    "请明确排名数量，使用 1 到 100 的整数。",
]
METRIC_QUESTION, DATE_QUESTION, INVALID_DATE_QUESTION, DIMENSION_QUESTION, FILTER_QUESTION, RANK_QUESTION = Clarification.__args__
RefusalCategory = Literal["write_request", "unauthorized_request", "unsupported_analysis"]
REFUSALS = {
    "write_request": "只支持只读经营查询，不能修改数据。",
    "unauthorized_request": "该请求超出批准的业务查询范围。",
    "unsupported_analysis": "本阶段不支持预测、因果、代码执行或多步分析。",
}
RefusalMessage = Literal[
    "只支持只读经营查询，不能修改数据。",
    "该请求超出批准的业务查询范围。",
    "本阶段不支持预测、因果、代码执行或多步分析。",
]


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "clarify", "refuse"]
    plan: AnalysisPlan | None = None
    clarification: Clarification | None = None
    refusal_category: RefusalCategory | None = None
    refusal_reason: RefusalMessage | None = None

    @model_validator(mode="before")
    @classmethod
    def _exclusive_fields(cls, value):
        if isinstance(value, cls):
            value = value.model_dump(exclude_none=True)
        if not isinstance(value, dict):
            raise ValueError("decision must be an object")
        allowed = {
            "ready": {"status", "plan"},
            "clarify": {"status", "clarification"},
            "refuse": {"status", "refusal_category", "refusal_reason"},
        }.get(value.get("status"))
        if allowed is None or set(value) != allowed:
            raise ValueError("decision fields do not match status")
        if value["status"] == "ready":
            value = {**value, "plan": parse_analysis_plan(value["plan"])}
        return value

    @model_validator(mode="after")
    def _required_payload(self):
        if self.status == "ready" and self.plan is None:
            raise ValueError("ready requires a validated plan")
        if self.status == "clarify" and not self.clarification:
            raise ValueError("clarify requires a question")
        if self.status == "refuse" and (self.refusal_category not in REFUSALS or self.refusal_reason != REFUSALS[self.refusal_category]):
            raise ValueError("refusal category and safe message must agree")
        return self


class DecisionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_decision(raw: object) -> PlannerDecision:
    """Revalidate even model_construct/model_copy objects; never expose raw errors."""
    if isinstance(raw, BaseModel):
        try:
            raw = raw.model_dump(exclude_none=True)
        except Exception:
            raise DecisionError("invalid_structure") from None
    if isinstance(raw, str):
        if len(raw) > MAX_OUTPUT_CHARS:
            raise DecisionError("invalid_structure")
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise DecisionError("invalid_json") from None
    try:
        return PlannerDecision.model_validate(raw)
    except Exception:
        raise DecisionError("invalid_structure") from None


def clarify(question: Clarification) -> PlannerDecision:
    return PlannerDecision(status="clarify", clarification=question)


def refuse(category: RefusalCategory) -> PlannerDecision:
    return PlannerDecision(status="refuse", refusal_category=category, refusal_reason=REFUSALS[category])


class RequestRecord(BaseModel):
    """Only these fields may be logged; no questions, prompts, plans or values."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    model_call_count: int = Field(ge=0, le=MAX_MODEL_CALLS)
    prompt_version: str = PROMPT_VERSION
    semantic_version: str
    model_name: str
    reference_date: str
    status: Literal["success", "clarification_required", "refused", "model_error", "plan_validation_error", "execution_error"]
    error_code: str | None = None
    validation_errors: tuple[str, ...] = ()
    query_id: str | None = None


class AgentResult(RequestRecord):
    decision_status: Literal["ready", "clarify", "refuse"] | None = None
    plan: AnalysisPlan | None = None
    analysis_result: AnalysisResult | None = None
    start_date: str | None = None
    end_date: str | None = None
    clarification: Clarification | None = None
    refusal_category: RefusalCategory | None = None
    refusal_reason: RefusalMessage | None = None
    error_message: str | None = None
    summary: str | None = None
    warnings: tuple[str, ...] = ()

    def audit_record(self) -> RequestRecord:
        return RequestRecord.model_validate({name: getattr(self, name) for name in RequestRecord.model_fields})
