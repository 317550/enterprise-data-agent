"""Offline demo grammar; scripted outputs remain useful for fault testing."""

import re

from eda.agent.dates import resolve_dates
from eda.agent.fake import FakePlannerModel
from eda.agent.models import (
    DATE_QUESTION, DIMENSION_QUESTION, FILTER_QUESTION, INVALID_DATE_QUESTION,
    METRIC_QUESTION, RANK_QUESTION, parse_decision,
)


class FakeConversationModel(FakePlannerModel):
    model_name = "fake-conversation-v1"

    def plan(self, question: str, context: dict) -> object:
        if self._outputs is not None:
            return super().plan(question, context)
        self.call_count += 1
        self.repair_errors.append(context.get("repair_error"))
        text = question.strip().rstrip("。？！?!")
        explicit_new = text.startswith("新话题：") or text.startswith("新话题:")
        if explicit_new:
            text = text[4:].strip()
        pending = context.get("pending")
        intent = "new" if explicit_new else "clarify_reply" if pending else "refine"

        def apply(values=None, **patch):
            return {"status": "apply", "intent": intent, "patch": {"set": values or {}, **patch}}

        clears = {"取消地区筛选": {"clear_filters": ["region"]},
                  "取消类别筛选": {"clear_filters": ["category"]},
                  "取消全部筛选": {"clear": ["filters"]},
                  "取消排名": {"clear": ["top_n"]},
                  "取消拆分": {"clear": ["dimension_id"]}}
        if text in clears:
            return apply(**clears[text])
        refined = text.startswith(("改成", "改为"))
        tail = text[2:].strip() if refined else text
        for metric in context["metrics"]:
            if tail in (metric["id"], metric["name"], *metric["synonyms"]):
                if not refined and not pending:
                    intent = "new"
                return apply({"metric_id": metric["id"]})
        for dimension in context["dimensions"]:
            if tail in tuple("按" + name for name in (dimension["id"], dimension["name"], *dimension["synonyms"])):
                return apply({"operation": "breakdown", "dimension_id": dimension["id"]})
        for dimension in context["dimensions"]:
            if dimension["id"] in {"region", "category"}:
                for value in dimension["allowed_values"] or ():
                    if text == "只看" + value:
                        return apply({"filters": [{"dimension_id": dimension["id"], "op": "eq", "value": value}]})
        if re.fullmatch(r"前\d{1,3}", tail):
            return apply({"top_n": int(tail[1:])})
        if tail in {"升序", "降序"}:
            return apply({"sort_direction": "asc" if tail == "升序" else "desc"})
        if re.fullmatch(r"(?:今年|去年|上个月|本月|这个月|\d{4}年(?:\d{1,2}月)?|\d{4}-\d{1,2}-\d{1,2}(?:至\d{4}-\d{1,2}-\d{1,2})?)", tail):
            return apply()
        if text == "继续":
            return apply()
        if refined:
            return {"status": "clarify", "intent": intent, "missing": ["intent"]}
        # A full standalone query starts a new topic. Reuse stage-three's
        # conservative parser; temporary dates permit an independent partial
        # draft, and are removed before returning the patch.
        intent = "new"
        dates = resolve_dates(text, context["reference_date"])
        demo_text = text
        if dates.issue == "missing_date":
            demo_text += " " + context["reference_date"]
        single = parse_decision(FakePlannerModel().plan(demo_text, context))
        if single.status == "refuse":
            return {"status": "refuse", "refusal_category": single.refusal_category}
        if single.status == "clarify":
            missing = {DATE_QUESTION: "dates", INVALID_DATE_QUESTION: "dates", METRIC_QUESTION: "metric_id",
                       DIMENSION_QUESTION: "dimension_id", FILTER_QUESTION: "filters", RANK_QUESTION: "top_n"}
            return {"status": "clarify", "intent": intent, "missing": [missing[single.clarification]]}
        values = single.plan.model_dump(exclude_none=True)
        values.pop("start_date")
        values.pop("end_date")
        return apply(values)
