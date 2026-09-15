"""Closed presentation vocabulary and recursive instance validation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")

    @classmethod
    def model_construct(cls, _fields_set=None, **values):
        instance = super().model_construct(_fields_set=_fields_set, **values)
        instance.__dict__.update(values)
        return instance

    @model_validator(mode="before")
    @classmethod
    def reparse(cls, value):
        def export(item):
            if isinstance(item, BaseModel):
                return {k: export(v) for k, v in {**item.__dict__, **(item.model_extra or {})}.items()}
            if isinstance(item, dict):
                return {k: export(v) for k, v in item.items()}
            if isinstance(item, tuple):
                return tuple(export(v) for v in item)
            if isinstance(item, list):
                return [export(v) for v in item]
            return item
        return export(value)


class ChartSpec(StrictModel):
    chart_type: Literal["bar", "line"]
    title: Literal["分类结果", "期间比较", "月度环比（仅两个期间）", "单维度变化贡献"]
    x_field: Literal["category", "period"]
    y_field: Literal["value", "change"]
    series_field: Literal["series"] | None = None
    x_kind: Literal["category", "temporal"]
    y_unit: Literal["分", "元", "分/单", "单", "人", "件", "%", "比例", "次"]
    sort_direction: Literal["asc", "desc", "none"]
    source_kind: Literal["single", "comparison", "contribution"]
    completeness: bool


class ViewModel(StrictModel):
    status: Literal["success", "clarification_required", "refused", "model_error", "plan_validation_error", "execution_error", "conversation_error"]
    message: str
    finding_type: Literal["observation", "decomposition"] | None = None
    metric: str = ""
    operation: str = ""
    unit: str = ""
    plan: dict = {}
    kpis: dict = {}
    rows: tuple[dict, ...] = ()
    chart: ChartSpec | None = None
    chart_rows: tuple[dict, ...] = ()
    completeness: dict = {}
    warnings: tuple[str, ...] = ()
    evidence: dict[str, str] = {}
    technical: dict = {}
    hidden: dict = {}
