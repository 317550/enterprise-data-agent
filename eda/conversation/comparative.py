"""Code-owned calendar extraction and controlled comparative inheritance."""

from dataclasses import dataclass
from datetime import date
import re

from eda.agent.dates import resolve_dates, strict_date
from eda.conversation.models import COMPARATIVE_OPERATIONS, Draft, Values
from eda.metrics.definitions import SEMANTIC
from eda.plan.comparative import PeriodSpec, month_period, previous_month, validate_as_of


@dataclass(frozen=True)
class ComparativeDates:
    current: PeriodSpec | None = None
    baseline: PeriodSpec | None = None
    issue: bool = False
    explicit: bool = False


def requested_operation(question):
    if "贡献" in question:
        return "contribution"
    if "环比" in question:
        return "mom"
    if "对比" in question or "比较" in question:
        return "compare"
    return None


def requested_dimensions(question):
    matches = [(d.id, token) for d in SEMANTIC.dimensions
               for token in (d.id, d.name_zh, *d.synonyms) if token in question
               and (d.id not in {"date", "month"} or "按" in question)]
    return tuple(dict.fromkeys(dimension for dimension, token in matches
        if not any(token != other and token in other for _, other in matches)))


def resolve_comparative_dates(question, reference_date, operation):
    reference = strict_date(reference_date)
    tokens = re.findall(r"(?<!\d)(\d{4})年(?:(\d{1,2})月)?", question)
    relative = [word for word in ("今年", "去年", "上个月", "本月", "这个月") if word in question]
    explicit = bool(tokens or relative or re.search(
        r"\d|日期|时间|期间|季度|最近|近期|过去|半月|半年|(?:本|上|下|每|近|这|去|今|前|后|同)[年月周天日]|[年月周日](?:初|末|中|底|内)", question))
    try:
        residue = re.sub(r"(?<!\d)\d{4}年(?:\d{1,2}月)?|(?:前|top\s*)\d+", "", question, flags=re.I)
        if re.search(r"\d", residue) or re.search(r"\d[日号]|\d{4}[-/]|季度|最近|过去|半月|半年|上旬|中旬|下旬|月初|月末|年初|年底|今天|昨日|本周|上周|基期|本期|反向|相反|截至|截止", question):
            raise ValueError("ambiguous or unsupported period/direction")
        if tokens and relative or len(relative) > 1:
            raise ValueError("mixed dates")
        periods = []
        for year, month in tokens:
            year = int(year)
            periods.append(month_period(year, int(month)) if month else PeriodSpec(
                start_date=date(year, 1, 1).isoformat(), end_date=date(year, 12, 31).isoformat(),
                label=f"{year}年", granularity="year", is_complete=True))
        if relative:
            window = resolve_dates(relative[0], reference_date)
            if window.issue or window.warnings:
                raise ValueError("unfinished relative period")
            periods.append(PeriodSpec(start_date=window.start_date, end_date=window.end_date,
                label=relative[0], granularity="year" if "年" in relative[0] else "month", is_complete=True))
        if not periods:
            return ComparativeDates(issue=explicit, explicit=explicit)
        if operation == "mom":
            if len(periods) != 1 or periods[0].granularity != "month":
                raise ValueError("one month required")
            current, baseline = periods[0], previous_month(periods[0])
        elif len(periods) == 2:
            baseline, current = sorted(periods, key=lambda p: p.start_date)
        else:
            raise ValueError("two explicit periods required")
        if current.granularity != baseline.granularity or baseline.end_date >= current.start_date or strict_date(current.end_date) > reference:
            raise ValueError("invalid periods")
        return ComparativeDates(current, baseline, explicit=True)
    except (ValueError, OverflowError):
        return ComparativeDates(issue=True, explicit=True)


def merge_comparative(session, decision, question):
    # Imported here to keep one public merge result and avoid a module cycle.
    from eda.conversation.merge import MergeResult, differences

    updates = decision.patch.set.model_dump(exclude_unset=True)
    if decision.intent == "refine":
        base = dict(session.confirmed or {}) if session.confirmed_type == "comparative" else {}
        missing = [] if base else ["history"]
    elif decision.intent == "clarify_reply":
        draft = session.pending
        base = draft.values.model_dump(exclude_none=True) if draft and draft.analysis_type == "comparative" else {}
        missing = [] if base else ["history"]
        if draft and (draft.intent is None or "intent" in draft.missing):
            missing.append("intent")
    else:
        base, missing = {}, []
    before = dict(base)
    operation = updates.get("operation", base.get("operation", requested_operation(question)))
    if operation not in COMPARATIVE_OPERATIONS:
        raise ValueError("comparative operation required")
    requested = requested_operation(question)
    if requested and requested != operation:
        raise ValueError("operation mismatch")
    window = resolve_comparative_dates(question, session.reference_date, operation)
    if any(key in updates for key in ("start_date", "end_date", "order_by")):
        raise ValueError("mixed analysis fields")
    if operation == "mom" and "baseline_period" in updates:
        raise ValueError("model cannot supply mom baseline")
    if not window.issue:
        for key, value in (("current_period", window.current), ("baseline_period", window.baseline)):
            if key in updates and (value is None or updates[key] != value.model_dump()):
                raise ValueError("model period mismatch")
    if window.issue:
        for key in ("current_period", "baseline_period"):
            base.pop(key, None)
            updates.pop(key, None)
        missing.append("dates")
    elif window.current:
        updates.update(current_period=window.current.model_dump(), baseline_period=window.baseline.model_dump())
    elif not base.get("current_period") or not base.get("baseline_period"):
        missing.append("dates")

    if operation == "contribution":
        dimensions = requested_dimensions(question)
        if re.search(r"合并|相加|求和|加总", question):
            missing.append("dimension_id")
        if requested != "contribution" or len(dimensions) != 1:
            missing.append("dimension_id")
        elif dimensions[0] not in {"region", "category"}:
            missing.append("dimension_id")
        elif "dimension_id" in updates and updates["dimension_id"] != dimensions[0]:
            raise ValueError("unrequested dimension")
        else:
            updates["dimension_id"] = dimensions[0]
        if before and not window.explicit:
            # A drilldown may change only the operation and one dimension.
            if decision.patch.clear or decision.patch.clear_filters or any(
                key not in {"operation", "dimension_id"} and value != before.get(key)
                for key, value in updates.items()
            ):
                raise ValueError("drilldown changed comparison context")
    elif updates.get("dimension_id") is not None or updates.get("top_n") is not None:
        raise ValueError("dimension is contribution-only")

    base.update(updates)
    base["operation"] = operation
    for key in decision.patch.clear:
        base.pop(key, None)
    if decision.patch.clear_filters:
        base["filters"] = tuple(item for item in base.get("filters", ()) if item["dimension_id"] not in decision.patch.clear_filters)
    if operation != "contribution":
        base.pop("dimension_id", None)
        base.pop("top_n", None)
    if not base.get("metric_id"):
        missing.append("metric_id")
    if not base.get("current_period") or not base.get("baseline_period"):
        missing.append("dates")
    if operation == "contribution" and not base.get("dimension_id"):
        missing.append("dimension_id")
    if base.get("metric_id") and operation == "contribution" and base.get("dimension_id"):
        if base["dimension_id"] not in SEMANTIC.metric(base["metric_id"]).contribution_dimensions:
            missing.append("dimension_id")
    missing.extend(decision.missing)
    if decision.intent is None:
        missing.append("intent")
    if missing:
        # Invalid periods/dimensions cannot be inherited on a clarification reply.
        if "dimension_id" in missing:
            base.pop("dimension_id", None)
            base.pop("top_n", None)
        return MergeResult(None, Draft(analysis_type="comparative", values=Values.model_validate(base),
            missing=tuple(dict.fromkeys(missing)), intent=decision.intent))
    plan = validate_as_of(base, session.reference_date)
    return MergeResult(plan, None, *differences(before, plan.model_dump(exclude_none=True)))
