"""One bounded planning repair; single query or deterministic comparative steps."""

from dataclasses import dataclass, field
import math
import time
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
    PROMPT_VERSION, STATE_VERSION, Missing, Session, StrictModel, TurnDecision, parse_turn,
)
from eda.metrics.definitions import SEMANTIC
from eda.plan.comparative import ComparativeAnalysisPlan, PeriodSpec
from eda.query import comparative as comparative_service
from eda.query.comparative_models import Comparison, Contribution, ComparativeEvidence, ComparativeCompleteness
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
    state_version: str = STATE_VERSION
    semantic_version: str = SEMANTIC.schema_version
    reference_date: str
    model_name: str
    query_count: int = Field(ge=0, le=4)
    node_path: tuple[str, ...] = ()
    analysis_operation: str | None = None
    baseline_period: PeriodSpec | None = None
    current_period: PeriodSpec | None = None
    comparison: Comparison | None = None
    contribution: Contribution | None = None
    evidence: tuple[ComparativeEvidence, ...] = ()
    completeness: ComparativeCompleteness | None = None
    finding_type: Literal["observation", "decomposition"] | None = None
    clarification: str | None = None
    model_call_count: int = Field(ge=0, le=MAX_MODEL_CALLS)
    error_code: str | None = None
    refusal_reason: str | None = None
    missing: tuple[Missing, ...] = ()
    plan: AnalysisPlan | ComparativeAnalysisPlan | None = None
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
    timeout_seconds: float = 30.0
    clock: object = time.monotonic
    deadline: float = field(init=False)
    node_path: list[str] = field(default_factory=list)
    comparative: object = None
    comparison: object = None
    contribution: object = None

    def __post_init__(self):
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("invalid request timeout")
        self.deadline = self.clock() + self.timeout_seconds

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0 or not math.isfinite(remaining):
            raise comparative_service._Stop("timeout")
        return remaining

    def finish(self, status, **payload):
        self.result = TurnResult(status=status, request_id=self.request_id,
                                 model_call_count=self.model_call_count,
                                 query_count=self.comparative.budget.query_count if self.comparative else self.execution_count,
                                 node_path=tuple(self.node_path), model_name=self.model.model_name,
                                 reference_date=self.base.reference_date,
                                 recovered=self.recovered, **payload)


def begin(state: GraphState, runtime: Runtime[TurnContext]) -> GraphState:
    session = Session.model_validate(state["session"])
    ctx = runtime.context
    ctx.node_path.append("begin")
    ctx.recovered = session.turn_status == "in_progress"
    ctx.base = session.model_copy(update={"confirmed": None, "pending": None}) if ctx.new_topic else session
    ctx.dates = resolve_dates(ctx.question, session.reference_date)
    # Keep single-turn safety policy unchanged; unlock only approved comparisons.
    inspected = ctx.question.replace("对比", "").replace("比较", "").replace("环比", "")
    blocked = preflight_refusal(inspected)
    if blocked:
        ctx.outcome, ctx.refusal_reason = "refused", blocked.refusal_reason
        if any(word in ctx.question for word in ("为什么", "原因", "因果", "导致", "归因")):
            ctx.refusal_reason = "无法判断原因"
    return {"session": session.model_copy(update={"turn_status": "in_progress"}).model_dump(exclude_none=True)}


def plan(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    """One transport call per visit; the merge edge permits at most one repair."""
    ctx = runtime.context
    ctx.node_path.append("plan")
    if ctx.model_call_count >= MAX_MODEL_CALLS:
        ctx.outcome, ctx.error_code = "plan_validation_error", "plan_generation_failed"
        return {}
    try:
        ctx.remaining()
        prompt = planning_context(ctx.base, ctx.repair_error)
        prompt["remaining_seconds"] = ctx.remaining()
        ctx.model_call_count += 1
        ctx.raw_response = ctx.model.plan(ctx.question, prompt)
        ctx.remaining()
    except comparative_service._Stop:
        ctx.outcome, ctx.error_code = "execution_error", "timeout"
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
    ctx.node_path.append("merge")
    ctx.candidate = None
    try:
        ctx.remaining()
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
        ctx.candidate = merge_decision(ctx.base, decision, ctx.dates, ctx.question)
        ctx.remaining()
    except comparative_service._Stop:
        ctx.outcome, ctx.error_code = "execution_error", "timeout"
        return {}
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
    ctx.node_path.append("execute")
    if ctx.execution_count:
        raise AssertionError("execute cannot be revisited")
    try:
        remaining = ctx.remaining()
        bounds = ctx.limits or ExecutionLimits.from_settings()
        bounds = ExecutionLimits.model_validate({**bounds.model_dump(), "timeout_seconds": min(bounds.timeout_seconds, remaining)})
        ctx.execution_count += 1
        ctx.analysis = run_analysis_plan(ctx.candidate.plan, ctx.db_path, limits=bounds)
        ctx.remaining()
        ctx.outcome = "success"
    except comparative_service._Stop:
        ctx.outcome, ctx.error_code = "execution_error", "timeout"
    except QueryError as exc:
        ctx.outcome, ctx.error_code = "execution_error", exc.code
    except Exception:
        ctx.outcome, ctx.error_code = "execution_error", "execution_failed"
    return {}


def _comparative_node(name, action, runtime):
    ctx = runtime.context
    ctx.node_path.append(name)
    try:
        ctx.remaining()
        action(ctx)
        ctx.remaining()
    except comparative_service._Stop as exc:
        ctx.outcome, ctx.error_code = "execution_error", exc.code
    except Exception:
        ctx.outcome, ctx.error_code = "execution_error", "execution_failed"
    return {}


def prepare_analysis(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    def prepare(ctx):
        ctx.comparative = comparative_service.prepare_analysis(ctx.candidate.plan,
            comparative_service.ComparativeExecutionLimits(query_limits=ctx.limits or ExecutionLimits.from_settings()),
            deadline=ctx.deadline, clock=ctx.clock)
    return _comparative_node("prepare_analysis", prepare, runtime)


def execute_step(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    return _comparative_node("execute_step", lambda ctx: comparative_service.execute_step(
        ctx.comparative, ctx.db_path, runner=run_analysis_plan, clock=ctx.clock), runtime)


def check_step(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    return _comparative_node("check_step", lambda ctx: comparative_service.check_step(
        ctx.comparative, clock=ctx.clock), runtime)


def calculate(state: GraphState, runtime: Runtime[TurnContext]) -> dict:
    def compute(ctx):
        ctx.comparison, ctx.contribution = comparative_service.calculate(ctx.comparative, clock=ctx.clock)
        ctx.outcome = "success"
    return _comparative_node("calculate", compute, runtime)


def finalize(state: GraphState, runtime: Runtime[TurnContext]) -> GraphState:
    """Render deterministic results; only success/clarification promotes state."""
    session = Session.model_validate(state["session"])
    ctx = runtime.context
    ctx.node_path.append("finalize")
    final = session.model_copy(update={"turn_status": "completed", "turn_count": session.turn_count + 1})
    if ctx.outcome == "clarification_required":
        final = final.model_copy(update={"pending": ctx.candidate.draft,
                                         "confirmed": None if ctx.decision.intent == "new" else session.confirmed})
        questions = {"dates": "请明确两个完整自然月或自然年；环比请指定一个已结束的自然月。" if ctx.candidate.draft.analysis_type == "comparative" else "请提供明确的查询日期。",
                     "metric_id": "请明确一个受批准指标。", "dimension_id": "请明确一个受批准且可加的贡献或拆分维度。",
                     "history": "没有可继承的对应分析，请提供完整问题。", "intent": "请明确是新话题还是继续上次分析。",
                     "filters": "请明确受批准的筛选条件。", "top_n": "请使用 1 到 100 的整数。"}
        ctx.finish(ctx.outcome, missing=ctx.candidate.draft.missing, warnings=ctx.dates.warnings,
                   clarification="".join(questions[key] for key in ctx.candidate.draft.missing))
    elif ctx.outcome == "success":
        merged, analysis = ctx.candidate, ctx.analysis
        is_comparative = isinstance(merged.plan, ComparativeAnalysisPlan)
        final = final.model_copy(update={"confirmed": merged.plan.model_dump(exclude_none=True), "pending": None,
                                         "confirmed_type": "comparative" if is_comparative else "single"})
        if is_comparative:
            comparative_result = comparative_service.render_result(ctx.comparative, ctx.comparison, ctx.contribution)
            ctx.finish("success", plan=merged.plan, analysis_operation=merged.plan.operation,
                baseline_period=merged.plan.baseline_period, current_period=merged.plan.current_period,
                comparison=ctx.comparison, contribution=ctx.contribution, evidence=tuple(ctx.comparative.evidence),
                completeness=comparative_result.completeness,
                finding_type="decomposition" if ctx.contribution else "observation",
                inherited=merged.inherited, changed=merged.changed, cleared=merged.cleared,
                warnings=comparative_result.warnings + ("贡献为单维度算术分解，无法判断原因。",))
            return {"session": Session.model_validate(final).model_dump(exclude_none=True)}
        warnings = ctx.dates.warnings + analysis.warnings
        if not analysis.completeness.is_complete_population:
            warnings += (analysis.completeness.note_zh,)
        ctx.finish("success", plan=merged.plan, analysis_result=analysis,
                   analysis_operation=merged.plan.operation,
                   inherited=merged.inherited, changed=merged.changed, cleared=merged.cleared,
                   warnings=warnings)
    else:
        ctx.finish(ctx.outcome, error_code=ctx.error_code, refusal_reason=ctx.refusal_reason)
    return {"session": Session.model_validate(final).model_dump(exclude_none=True)}


def after_begin(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "plan"


def after_plan(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "merge"


def after_merge(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    ctx = runtime.context
    if ctx.outcome:
        return "finalize"
    if ctx.repair_error and ctx.model_call_count < MAX_MODEL_CALLS:
        return "plan"
    return "prepare_analysis" if isinstance(ctx.candidate.plan, ComparativeAnalysisPlan) else "execute"


def after_prepare(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "execute_step"


def after_step(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return "finalize" if runtime.context.outcome else "check_step"


def after_check(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    ctx = runtime.context
    if ctx.outcome:
        return "finalize"
    if ctx.comparative.has_next:
        if ctx.comparative.budget.query_count >= ctx.comparative.budget.max_queries:
            ctx.outcome, ctx.error_code = "execution_error", "budget_exhausted"
            return "finalize"
        return "execute_step"
    return "calculate"


def after_execute(state: GraphState, runtime: Runtime[TurnContext]) -> str:
    return runtime.context.outcome


def build_graph(checkpointer=None):
    builder = StateGraph(GraphState, context_schema=TurnContext)
    builder.add_node("begin", begin)
    builder.add_node("plan", plan)
    builder.add_node("merge", merge)
    builder.add_node("execute", execute)
    builder.add_node("finalize", finalize)
    builder.add_node("prepare_analysis", prepare_analysis)
    builder.add_node("execute_step", execute_step)
    builder.add_node("check_step", check_step)
    builder.add_node("calculate", calculate)
    builder.add_edge(START, "begin")
    builder.add_conditional_edges("begin", after_begin, ["plan", "finalize"])
    builder.add_conditional_edges("plan", after_plan, ["merge", "finalize"])
    builder.add_conditional_edges("merge", after_merge, ["plan", "execute", "prepare_analysis", "finalize"])
    builder.add_conditional_edges("prepare_analysis", after_prepare, ["execute_step", "finalize"])
    builder.add_conditional_edges("execute_step", after_step, ["check_step", "finalize"])
    builder.add_conditional_edges("check_step", after_check, ["execute_step", "calculate", "finalize"])
    builder.add_edge("calculate", "finalize")
    builder.add_conditional_edges("execute", after_execute, {"success": "finalize", "execution_error": "finalize"})
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
