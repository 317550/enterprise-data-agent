"""Pure, code-owned inheritance and differences; full plans stay strict."""

from dataclasses import dataclass

from eda.agent.dates import DateWindow
from eda.plan.models import AnalysisPlan, parse_analysis_plan
from eda.conversation.models import Draft, Session, TurnDecision, Values
from eda.conversation.models import COMPARATIVE_OPERATIONS
from eda.plan.comparative import ComparativeAnalysisPlan


@dataclass
class MergeResult:
    plan: AnalysisPlan | ComparativeAnalysisPlan | None
    draft: Draft | None
    inherited: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    cleared: tuple[str, ...] = ()


def merge_decision(session: Session, decision: TurnDecision, dates: DateWindow, question: str = "") -> MergeResult:
    if decision.status == "refuse":
        raise ValueError("refusal cannot be merged")
    from eda.conversation.comparative import merge_comparative, requested_operation
    operation = decision.patch.set.operation
    comparative_history = ((decision.intent == "refine" and session.confirmed_type == "comparative" and session.confirmed)
        or (decision.intent == "clarify_reply" and session.pending and session.pending.analysis_type == "comparative"))
    if operation in COMPARATIVE_OPERATIONS or requested_operation(question) or (comparative_history and operation is None):
        return merge_comparative(session, decision, question)
    if decision.patch.set.current_period or decision.patch.set.baseline_period:
        raise ValueError("single analysis cannot contain comparative periods")
    if comparative_history:
        # Crossing analysis types requires a fresh plan; no comparative fields leak.
        session = session.model_copy(update={"confirmed": None, "pending": None, "confirmed_type": "single"})
    if decision.intent == "refine":
        base = dict(session.confirmed or {})
        missing = ["history"] if session.confirmed is None else []
    elif decision.intent == "clarify_reply":
        base = session.pending.values.model_dump(exclude_none=True) if session.pending else {}
        missing = ["history"] if session.pending is None else []
        if session.pending and (session.pending.intent is None or "intent" in session.pending.missing):
            missing.append("intent")
    else:
        base, missing = {}, []
    before = dict(base)
    updates = decision.patch.set.model_dump(exclude_unset=True)
    # Explicit input dates are authoritative; invalid dates never fall back.
    if dates.issue and dates.issue != "missing_date":
        base.pop("start_date", None)
        base.pop("end_date", None)
        updates.pop("start_date", None)
        updates.pop("end_date", None)
        missing.append("dates")
    elif dates.start_date:
        for key in ("start_date", "end_date"):
            if key in updates and updates[key] != getattr(dates, key):
                raise ValueError("date mismatch")
            updates[key] = getattr(dates, key)
    elif "start_date" in updates or "end_date" in updates:
        raise ValueError("dates not provided by question")
    base.update(updates)
    for field in decision.patch.clear:
        base.pop(field, None)
    if decision.patch.clear_filters:
        base["filters"] = [clause for clause in base.get("filters", ())
                           if clause["dimension_id"] not in decision.patch.clear_filters]
    if "dimension_id" in decision.patch.clear:
        base["operation"] = "total"
        for field in ("top_n", "order_by", "sort_direction"):
            base.pop(field, None)
    if base.get("operation") == "total":
        for field in ("dimension_id", "top_n", "order_by", "sort_direction"):
            if field not in updates:
                base.pop(field, None)
    base.setdefault("operation", "total")
    if not base.get("metric_id"):
        missing.append("metric_id")
    if not base.get("start_date") or not base.get("end_date"):
        missing.append("dates")
    if base["operation"] == "breakdown" and not base.get("dimension_id"):
        missing.append("dimension_id")
    missing.extend(decision.missing)
    if decision.intent is None:
        missing.append("intent")
    if missing:
        return MergeResult(None, Draft(values=Values.model_validate(base), missing=tuple(dict.fromkeys(missing)), intent=decision.intent))
    plan = parse_analysis_plan(base)
    after = plan.model_dump(exclude_none=True)
    return MergeResult(plan, None, *differences(before, after))


def differences(before, after):
    # Compare leaf filter dimensions so selective clear is visible, not model prose.
    def flattened(value):
        return {**{key: item for key, item in value.items() if key != "filters"},
                **{f"filters.{item['dimension_id']}": item for item in value.get("filters", ())}}
    old, new = flattened(before), flattened(after)
    return (tuple(sorted(key for key in old if key in new and old[key] == new[key])),
                       tuple(sorted(key for key in new if key not in old or old[key] != new[key])),
                       tuple(sorted(key for key in old if key not in new)))
