"""Five explicit nodes; one bounded plan repair and one execution per turn."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import Field

from eda.agent.dates import DateWindow, resolve_dates
from eda.agent.models import MAX_MODEL_CALLS, REFUSALS
from eda.agent.planner import ModelFailure, PlannerModel, preflight_refusal
from eda.conversation.context import planning_context
from eda.conversation.merge import MergeResult, merge_decision
from eda.conversation.models import (
    PROMPT_VERSION, Missing, Session, StrictModel, TurnDecision, parse_turn,
)
from eda.plan.models import AnalysisPlan
from eda.query.errors import QueryError
from eda.query.models import AnalysisResult
from eda.query.service import run_analysis_plan
from eda.sql.executor import ExecutionLimits


class TurnResult(StrictModel):
    status: Literal["success", "clarification_required", "refused", "model_error",
                    "plan_validation_error", "execution_error"]
    request_id: str
    prompt_version: str = PROMPT_VERSION
    model_call_count: int = Field(ge=0, le=MAX_MODEL_CALLS)
    error_code: str | None = None
    refusal_reason: str | None = None
    missing: tuple[Missing, ...] = ()
    plan: AnalysisPlan | None = None
    analysis_result: AnalysisResult | None = None
    inherited: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    cleared: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    recovered: bool = False


class GraphState(TypedDict):
    session: dict


@dataclass
class TurnContext:
    question: str
    db_path: Path
    model: PlannerModel
    limits: ExecutionLimits | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)
    model_call_count: int = 0
    raw_response: object = None
    candidate: MergeResult | None = None
    result: TurnResult | None = None
    recovered: bool = False
    new_topic: bool = False
    base: Session | None = None
    dates: DateWindow | None = None
    decision: TurnDecision | None = None
    analysis: AnalysisResult | None = None
    outcome: str | None = None
    error_code: str | None = None
    refusal_reason: str | None = None
    repair_error: str | None = None
    execution_count: int = 0

    def finish(self, status, **payload):
        self.result = TurnResult(status=status, request_id=self.request_id,
                                 model_call_count=self.model_call_count,
                                 recovered=self.recovered, **payload)


def begin(state: GraphState, runtime: Runtime[TurnContext]) -> GraphState:
    session = Session.model_validate(state["session"])
    ctx = runtime.context
    ctx.recovered = session.turn_status == "in_progress"
    ctx.base = session.model_copy(update={"confirmed": None, "pending": None}) if ctx.new_topic else session
    ctx.dates = resolve_dates(ctx.question, session.reference_date)
    blocked = preflight_refusal(ctx.question)
    if blocked:
        ctx.outcome, ctx.refusal_reason = "refused", blocked.refusal_reason
    return {"session": session.model_copy(update={"turn_status": "in_progress"}).model_dump(exclude_none=True)}


def plan(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    """One transport call per visit; the merge edge permits at most one repair."""
    ctx = runtime.context
    if ctx.model_call_count >= MAX_MODEL_CALLS:
        ctx.outcome, ctx.error_code = "plan_validation_error", "plan_generation_failed"
        return {}
    ctx.model_call_count += 1
    try:
        ctx.raw_response = ctx.model.plan(ctx.question, planning_context(ctx.base, ctx.repair_error))
    except TimeoutError:
        ctx.outcome, ctx.error_code = "model_error", "model_timeout"
    except ModelFailure as exc:
        ctx.outcome, ctx.error_code = "model_error", exc.code
    except Exception:
        ctx.outcome, ctx.error_code = "model_error", "model_unavailable"
    return {}


def merge(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    """Validate the turn protocol and merge only approved business fields."""
    ctx = runtime.context
    ctx.candidate = None
    try:
        decision = parse_turn(ctx.raw_response)
        if decision.status == "refuse":
            ctx.outcome, ctx.refusal_reason = "refused", REFUSALS[decision.refusal_category]
            return {}
        if ctx.new_topic:
            # The explicit flag wins over model guesses, without changing the
            # persisted pre-turn state used by finalize on failure.
            payload = decision.model_dump(exclude_none=True, exclude_unset=True)
            payload["intent"] = "new"
            if decision.status == "clarify":
                payload["missing"] = [item for item in decision.missing if item not in {"intent", "history"}]
                if not payload["missing"]:
                    payload.pop("missing")
                    payload["status"] = "apply"
            decision = parse_turn(payload)
        ctx.decision = decision
        ctx.candidate = merge_decision(ctx.base, decision, ctx.dates)
    except (ValueError, QueryError):
        ctx.repair_error = "invalid_plan"
        if ctx.model_call_count >= MAX_MODEL_CALLS:
            ctx.outcome, ctx.error_code = "plan_validation_error", "plan_generation_failed"
        return {}
    ctx.repair_error = None
    if ctx.candidate.draft:
        ctx.outcome = "clarification_required"
    return {}


def execute(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    """One invocation of the existing safe query service, with no model repair."""
    ctx = runtime.context
    if ctx.execution_count:
        raise AssertionError("execute cannot be revisited")
    ctx.execution_count += 1
    try:
        ctx.analysis = run_analysis_plan(ctx.candidate.plan, ctx.db_path, limits=ctx.limits)
        ctx.outcome = "success"
    except QueryError as exc:
        ctx.outcome, ctx.error_code = "execution_error", exc.code
    except Exception:
        ctx.outcome, ctx.error_code = "execution_error", "execution_failed"
    return {}


def finalize(state: GraphState, runtime: Runtime[TurnContext]) -> GraphState:
    """Render deterministic results; only success/clarification promotes state."""
    session = Session.model_validate(state["session"])
    ctx = runtime.context
    final = session.model_copy(update={"turn_status": "completed", "turn_count": session.turn_count + 1})
    if ctx.outcome == "clarification_required":
        final = final.model_copy(update={"pending": ctx.candidate.draft,
                                         "confirmed": None if ctx.decision.intent == "new" else session.confirmed})
        ctx.finish(ctx.outcome, missing=ctx.candidate.draft.missing, warnings=ctx.dates.warnings)
    elif ctx.outcome == "success":
        merged, analysis = ctx.candidate, ctx.analysis
        final = final.model_copy(update={"confirmed": merged.plan.model_dump(exclude_none=True), "pending": None})
        warnings = ctx.dates.warnings + analysis.warnings
        if not analysis.completeness.is_complete_population:
            warnings += (analysis.completeness.note_zh,)
        ctx.finish("success", plan=merged.plan, analysis_result=analysis,
                   inherited=merged.inherited, changed=merged.changed, cleared=merged.cleared,
                   warnings=warnings)
    else:
        ctx.finish(ctx.outcome, error_code=ctx.error_code, refusal_reason=ctx.refusal_reason)
    return {"session": final.model_dump(exclude_none=True)}


def after_begin(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "plan"


def after_plan(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "merge"


def after_merge(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    ctx = runtime.context
    if ctx.outcome:
        return "finalize"
    return "plan" if ctx.repair_error and ctx.model_call_count < MAX_MODEL_CALLS else "execute"


def after_execute(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return runtime.context.outcome


def build_graph(checkpointer=None):
    builder = StateGraph(GraphState, context_schema=TurnContext)
    builder.add_node("begin", begin)
    builder.add_node("plan", plan)
    builder.add_node("merge", merge)
    builder.add_node("execute", execute)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "begin")
    builder.add_conditional_edges("begin", after_begin, ["plan", "finalize"])
    builder.add_conditional_edges("plan", after_plan, ["merge", "finalize"])
    builder.add_conditional_edges("merge", after_merge, ["plan", "execute", "finalize"])
    builder.add_conditional_edges("execute", after_execute, {"success": "finalize", "execution_error": "finalize"})
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
