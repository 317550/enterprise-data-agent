"""Offline planner double: scripted faults or a small, conservative demo grammar.

It produces business plans only; it has no data, expected answers or SQL.
The grammar is a deterministic demonstration, not a general NLU implementation.
"""

import re
from collections.abc import Sequence

from eda.agent.dates import resolve_dates
from eda.agent.models import (
    DATE_QUESTION, DIMENSION_QUESTION, FILTER_QUESTION, INVALID_DATE_QUESTION,
    METRIC_QUESTION, RANK_QUESTION, clarify, refuse,
)
from eda.agent.planner import preflight_refusal


class FakePlannerModel:
    model_name = "fake-planner-v1"

    def __init__(self, outputs: Sequence[object] | None = None):
        self._outputs = None if outputs is None else iter(outputs)
        self.call_count = 0
        self.repair_errors: list[str | None] = []

    def plan(self, question: str, context: dict) -> object:
        self.call_count += 1
        self.repair_errors.append(context.get("repair_error"))
        if self._outputs is not None:
            output = next(self._outputs)
            if isinstance(output, Exception):
                raise output
            return output
        blocked = preflight_refusal(question)
        if blocked is not None:
            return blocked
        window = resolve_dates(question, context["reference_date"])
        if window.issue:
            return clarify(DATE_QUESTION if window.issue == "missing_date" else INVALID_DATE_QUESTION)
        metrics = [metric for metric in context["metrics"]
                   if any(name.lower() in question.lower() for name in (metric["id"], metric["name"], *metric["synonyms"]))]
        # Long aliases may contain another metric's short alias. Prefer the
        # longest matching phrase only when it actually contains the shorter.
        matches = [(metric, max((name for name in (metric["id"], metric["name"], *metric["synonyms"])
                                if name.lower() in question.lower()), key=len)) for metric in metrics]
        metrics = [metric for metric, name in matches
                   if not any(name != other and name in other for _, other in matches)]
        if len(metrics) != 1:
            if any(word in question for word in ("利润", "净收入", "退款金额", "取消率")):
                return refuse("unsupported_analysis")
            return clarify(METRIC_QUESTION)
        dimensions = [dimension for dimension in context["dimensions"]
                      if any(name in question for name in (dimension["id"], dimension["name"], *dimension["synonyms"]))]
        dim_matches = [(dimension, max((name for name in (dimension["id"], dimension["name"], *dimension["synonyms"])
                                        if name in question), key=len)) for dimension in dimensions]
        dimensions = [dimension for dimension, name in dim_matches
                      if not any(name != other and name in other for _, other in dim_matches)]
        # Explicit dates are not an instruction to group by date/month.
        dimensions = [dimension for dimension in dimensions if dimension["id"] not in {"date", "month"}
                      or any(word in question for word in ("按日", "每日", "各日", "按天", "每天", "日粒度", "按月", "每月", "各月", "月份", "月度", "日期拆分"))]
        if len(dimensions) > 1:
            return clarify(DIMENSION_QUESTION)
        filters = []
        for dimension in context["dimensions"]:
            values = [value for value in dimension["allowed_values"] or () if value in question]
            if values:
                if "filter" not in dimension["operations"]:
                    return clarify(FILTER_QUESTION)
                filters.append({"dimension_id": dimension["id"], "op": "eq" if len(values) == 1 else "in",
                                "value": values[0] if len(values) == 1 else values})
        if any(word in question for word in ("排除", "不含", "除了", "不是")):
            return clarify(FILTER_QUESTION)
        ranked = re.search(r"(?:前|top\s*)(-?\d+)", question, re.I)
        if ("排名" in question or "排行" in question or ranked) and not dimensions:
            return clarify(DIMENSION_QUESTION)
        if "前" in question and not ranked:
            return clarify(RANK_QUESTION)
        if _has_unrecognized_text(question, context):
            return clarify(FILTER_QUESTION)
        payload = {"metric_id": metrics[0]["id"], "operation": "breakdown" if dimensions else "total",
                   "start_date": window.start_date, "end_date": window.end_date, "filters": filters}
        if dimensions:
            payload.update(dimension_id=dimensions[0]["id"], order_by="metric_value",
                           sort_direction="asc" if any(word in question for word in ("最低", "升序", "从低到高")) else "desc")
        if ranked:
            payload["top_n"] = int(ranked[1])
        return {"status": "ready", "plan": payload}


def _has_unrecognized_text(question, context):
    """Do not silently drop an unknown location, filter, or instruction in fake mode."""
    text = question.lower()
    vocabulary = []
    for entry in (*context["metrics"], *context["dimensions"]):
        vocabulary.extend((entry["id"], entry["name"], *entry["synonyms"]))
        vocabulary.extend(entry.get("allowed_values") or ())
    for token in sorted(vocabulary, key=len, reverse=True):
        text = text.replace(token.lower(), "")
    text = re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{4}年(?:\d{1,2}月(?:\d{1,2}[日号])?)?", "", text)
    text = re.sub(r"(?:前|top\s*)-?\d+", "", text)
    for token in ("这个月", "上个月", "今年", "去年", "本月", "是多少", "有多少", "怎么样", "多少",
                  "从高到低", "从低到高", "降序", "升序", "最高", "最低", "总量", "总计", "总额", "合计",
                  "排名", "排行", "拆分", "查询", "统计", "查看", "请问", "请", "帮我", "各个", "每个", "各", "按",
                  "的", "和", "或", "与", "及", "至", "到", "从", "是", "为", "呢", "项", "名", "个", "分别", "全年的", "全年"):
        text = text.replace(token, "")
    return bool(re.sub(r"[\s，。？！?！、,.;:：=（）()]+", "", text))
