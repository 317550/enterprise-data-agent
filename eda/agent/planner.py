"""Minimal planner interface and conservative preflight refusal policy."""

import re
from typing import Protocol

from eda.agent.models import PlannerDecision, refuse


class PlannerModel(Protocol):
    model_name: str

    def plan(self, question: str, context: dict) -> object:
        """Return an untrusted JSON object/string or decision to be revalidated."""
        ...


class ModelFailure(Exception):
    def __init__(self, code: str):
        self.code = code if code in {"model_timeout", "model_unavailable", "model_configuration_error"} else "model_unavailable"
        super().__init__(self.code)


def preflight_refusal(question: str) -> PlannerDecision | None:
    """Early budget guard, not a replacement for model/plan/execution validation."""
    if any(word in question for word in ("删除", "修改", "更新数据", "插入", "新增订单", "写入", "清空", "删掉")) or re.search(r"\b(insert|update|delete|drop|alter|create|truncate)\b", question, re.I | re.ASCII):
        return refuse("write_request")
    if any(word in question for word in ("密码", "密钥", "身份证", "手机号", "数据库结构", "系统表", "全部表")) or re.search(r"\b(sql|select|pragma|attach|sqlite_master|sqlite_schema)\b", question, re.I | re.ASCII):
        return refuse("unauthorized_request")
    if any(word in question for word in ("预测", "预计", "为什么", "原因", "因果", "导致", "归因", "环比", "同比", "贡献拆解", "下钻", "对比", "比较", "代码", "脚本")) or re.search(r"\b(python|eval|exec|forecast|predict)\b", question, re.I | re.ASCII):
        return refuse("unsupported_analysis")
    return None
