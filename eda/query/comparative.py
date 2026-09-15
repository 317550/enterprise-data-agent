"""Bounded sequential execution; no SQL, model, graph, or persistence layer."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import math
import time
import uuid

from pydantic import Field, field_validator

from eda.metrics.comparative import calculate_comparison, calculate_contribution, _decimal
from eda.metrics.definitions import METRIC_REGISTRY, SEMANTIC
from eda.plan.comparative import StrictComparativeModel, _export
from eda.plan.comparative_execution import compile_comparative_plan
from eda.query.comparative_models import (
    ComparativeAnalysisResult, ComparativeCompleteness, ComparativeEvidence,
    Comparison, Contribution, ContributionDetail,
)
from eda.query.errors import QueryError
from eda.query.models import AnalysisResult
from eda.query.service import run_analysis_plan
from eda.sql.executor import ExecutionLimits


class ComparativeExecutionLimits(StrictComparativeModel):
    timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    max_queries: int = Field(default=4, ge=1, le=4)
    query_limits: ExecutionLimits = Field(default_factory=ExecutionLimits.from_settings)

    @field_validator("query_limits", mode="before")
    @classmethod
    def revalidate_limits(cls, value):
        return ExecutionLimits.model_validate(_export(value), strict=True)


class _Stop(Exception):
    def __init__(self, code, source=None):
        self.code = code
        self.source = source


@dataclass
class ComparativeExecutionBudget:
    """One request-owned mutable ledger, never reset between steps."""

    max_queries: int
    deadline: float
    query_count: int = 0

    def remaining(self, clock):
        remaining = self.deadline - clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise _Stop("timeout")
        return remaining

    def attempt(self, clock):
        remaining = self.remaining(clock)
        if self.query_count >= self.max_queries:
            raise _Stop("budget_exhausted")
        self.query_count += 1
        return remaining


def _number(value):
    if value is None:
        raise _Stop("undefined_metric")
    if type(value) not in (int, float):
        raise _Stop("invalid_result_shape")
    try:
        return _decimal(Decimal(str(value)))
    except (ValueError, ArithmeticError):
        raise _Stop("invalid_result_shape") from None


def _check(result, step, seen_ids):
    if not isinstance(result, AnalysisResult):
        raise _Stop("invalid_result_shape")
    if (getattr(result, "query_id", None) is None
            or getattr(getattr(result, "execution", None), "query_id", None) is None):
        raise _Stop("missing_query_id")
    # Inspect raw instances first: coercion must not hide forged booleans/numbers.
    try:
        result = AnalysisResult.model_validate(_export(result), strict=True)
    except Exception:
        raise _Stop("invalid_result_shape") from None
    execution, complete, plan = result.execution, result.completeness, step.analysis_plan
    if execution.status != "ok" or execution.error_code is not None:
        raise _Stop("execution_error")
    if not result.query_id.strip() or not execution.query_id.strip():
        raise _Stop("missing_query_id")
    if result.query_id != execution.query_id or result.query_id in seen_ids:
        raise _Stop("metadata_mismatch")
    if execution.truncated or complete.truncated:
        raise _Stop("incomplete_result")
    if not complete.is_complete_population or complete.ranked_top_n is not None:
        raise _Stop("incomplete_result")
    definition = METRIC_REGISTRY[plan.metric_id]
    expected = dict(
        semantic_version=SEMANTIC.schema_version, metric_id=plan.metric_id,
        metric_name_zh=definition.name_zh, operation=plan.operation,
        start_date=plan.start_date, end_date=plan.end_date, dimension_id=plan.dimension_id,
        unit=definition.unit, unit_code=definition.unit_code,
        filters=tuple(clause.model_dump(exclude={"value"}) for clause in plan.filters),
    )
    if any(getattr(result, key) != value for key, value in expected.items()):
        raise _Stop("metadata_mismatch")
    columns = ("metric_value",) if plan.operation == "total" else ("dimension_value", "metric_value")
    if (execution.columns != columns or execution.row_count != len(result.rows)
            or execution.truncation_reason is not None or complete.truncation_reason is not None):
        raise _Stop("invalid_result_shape")
    if plan.operation == "total":
        if len(result.rows) != 1 or result.rows[0].dimension_value is not None:
            raise _Stop("invalid_result_shape")
        _number(result.rows[0].metric_value)
    else:
        seen = set()
        allowed = SEMANTIC.dimension(plan.dimension_id).allowed_values
        for row in result.rows:
            key = row.dimension_value
            if (type(key) is not str or not key.strip() or len(key) > 128 or key in seen
                    or allowed is not None and key not in allowed):
                raise _Stop("invalid_result_shape")
            seen.add(key)
            _number(row.metric_value)
    return result


@dataclass
class ComparativeRun:
    """Transient step cursor shared by the public runner and the conversation graph."""

    execution_plan: object
    bounds: ComparativeExecutionLimits
    budget: ComparativeExecutionBudget
    index: int = 0
    pending_result: object = None
    evidence: list = field(default_factory=list)
    results: dict = field(default_factory=dict)

    @property
    def has_next(self):
        return self.index < len(self.execution_plan.steps)


def prepare_analysis(plan, limits=None, *, deadline=None, clock=time.monotonic):
    started = clock() if deadline is None else None
    try:
        execution_plan = compile_comparative_plan(plan)
    except Exception:
        raise _Stop("invalid_plan") from None
    try:
        bounds = ComparativeExecutionLimits.model_validate(
            limits if limits is not None else ComparativeExecutionLimits())
    except Exception:
        raise _Stop("invalid_limits", execution_plan.plan) from None
    budget = ComparativeExecutionBudget(
        min(bounds.max_queries, len(execution_plan.steps)),
        started + bounds.timeout_seconds if deadline is None else deadline,
    )
    return ComparativeRun(execution_plan, bounds, budget)


def execute_step(run, db_path, runner=run_analysis_plan, *, clock=time.monotonic):
    if not run.has_next or run.pending_result is not None:
        raise _Stop("budget_exhausted")
    remaining = run.budget.remaining(clock)
    per_query = ExecutionLimits.model_validate(dict(
        **run.bounds.query_limits.model_dump(exclude={"timeout_seconds"}),
        timeout_seconds=min(run.bounds.query_limits.timeout_seconds, remaining),
    ))
    run.budget.attempt(clock)
    try:
        result = runner(run.execution_plan.steps[run.index].analysis_plan, db_path, limits=per_query)
    except Exception as exc:
        run.budget.remaining(clock)
        raise _Stop("timeout" if isinstance(exc, QueryError) and exc.code == "timeout"
                    else "execution_error") from None
    run.budget.remaining(clock)
    run.pending_result = result


def check_step(run, *, clock=time.monotonic):
    run.budget.remaining(clock)
    step = run.execution_plan.steps[run.index]
    result = _check(run.pending_result, step, {item.query_id for item in run.evidence})
    run.results[step.role] = result
    run.evidence.append(ComparativeEvidence(
        **step.model_dump(), query_id=result.query_id, row_count=len(result.rows),
    ))
    run.pending_result = None
    run.index += 1


def calculate(run, *, clock=time.monotonic):
    run.budget.remaining(clock)
    if run.has_next or run.pending_result is not None:
        raise _Stop("incomplete_result")
    comparison, contribution = _calculate(run.execution_plan.plan, run.results)
    run.budget.remaining(clock)
    return comparison, contribution


def _calculate(source, results):
    contribution = None
    baseline = _number(results["baseline_total"].rows[0].metric_value)
    current = _number(results["current_total"].rows[0].metric_value)
    calculated = calculate_comparison(current, baseline)
    if source.operation == "contribution":
        maps = {role: {row.dimension_value: _number(row.metric_value) for row in results[role].rows}
                for role in ("baseline_breakdown", "current_breakdown")}
        calculated_contribution = calculate_contribution(
            source, maps["current_breakdown"], maps["baseline_breakdown"],
            current_total=current, baseline_total=baseline,
        )
        if calculated_contribution.status != "success":
            raise _Stop("reconciliation_failed")
        contribution = Contribution(
            dimension_id=calculated_contribution.dimension_id,
            reconciled_change=calculated_contribution.reconciled_change,
            rows=tuple(ContributionDetail(**asdict(row)) for row in calculated_contribution.rows),
            hidden_dimension_count=calculated_contribution.hidden_dimension_count,
            hidden_net_change=calculated_contribution.hidden_net_change,
        )
    comparison = Comparison(**asdict(calculated), baseline_period=source.baseline_period,
                            current_period=source.current_period)
    return comparison, contribution


def run_comparative_analysis(
    plan, db_path, limits=None, runner=run_analysis_plan, *, clock=time.monotonic,
) -> ComparativeAnalysisResult:
    """Runner is trusted infrastructure, not a model-controlled input.

    Existing results redact filter values. We check the available filter schema
    and retain the validated dispatched plan as evidence of bindings requested.
    Deadlines are cooperative: a misbehaving injected runner cannot be killed.
    """
    analysis_id = uuid.uuid4().hex
    source = None
    run = None
    comparison = contribution = None
    error = None
    try:
        run = prepare_analysis(plan, limits, clock=clock)
        source = run.execution_plan.plan
        while run.has_next:
            execute_step(run, db_path, runner, clock=clock)
            check_step(run, clock=clock)
        comparison, contribution = calculate(run, clock=clock)
    except _Stop as exc:
        error = exc.code
        source = source or exc.source
        comparison = contribution = None
    except (ValueError, ArithmeticError, TypeError, AttributeError):
        error = "invalid_result_shape"
        comparison = contribution = None
    return render_result(run, comparison, contribution, error, source=source, analysis_id=analysis_id)


def render_result(run, comparison=None, contribution=None, error=None, *, source=None, analysis_id=None):
    source = run.execution_plan.plan if run else source
    definition = METRIC_REGISTRY[source.metric_id] if source else None
    return ComparativeAnalysisResult(
        analysis_id=analysis_id or uuid.uuid4().hex, semantic_version=SEMANTIC.schema_version,
        metric_id=source.metric_id if source else None,
        metric_name_zh=definition.name_zh if definition else None,
        unit=definition.unit if definition else None,
        unit_code=definition.unit_code if definition else None,
        operation=source.operation if source else None,
        baseline_period=source.baseline_period if source else None,
        current_period=source.current_period if source else None,
        query_count=run.budget.query_count if run else 0, evidence=tuple(run.evidence) if run else (),
        comparison=comparison, contribution=contribution, error_code=error,
        completeness=ComparativeCompleteness(
            is_complete_population=error is None,
            display_is_complete_population=error is None and (
                contribution is None or contribution.hidden_dimension_count == 0),
            calendar_period_complete=source is not None,
        ),
        warnings=(
            "calendar_period_complete=" + ("true" if source else "false"),
            "data_coverage_verified=false：完整日历期间不等于已证明数据库数据无缺口。",
            "筛选值由已验证子计划保留；阶段二结果仅返回脱敏筛选元数据。",
            "小数通过 Decimal(str(value)) 接收；上游 SQL 比率可能已经浮点近似。",
        ) + (("top_n 仅截取展示；展示子集不是完整贡献总体。",)
             if contribution is not None and contribution.hidden_dimension_count else ()),
    )
