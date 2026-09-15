"""Audit schema deliberately cannot contain business payloads."""
from datetime import datetime, timezone
from hashlib import sha256
from typing import Annotated, Literal
from pydantic import Field
from eda.viz.models import StrictModel

Id = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Token = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")]
Node = Literal["begin", "plan", "merge", "execute", "finalize", "prepare_analysis", "execute_step", "check_step", "calculate"]
Evidence = Literal["single_result", "baseline_total", "current_total", "baseline_breakdown", "current_breakdown"]


class AuditRecord(StrictModel):
    run_id: Id
    created_at: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")]
    thread_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    request_id: Id
    status: Literal["success", "clarification_required", "refused", "model_error", "plan_validation_error", "execution_error", "conversation_error"]
    analysis_operation: Literal["total", "breakdown", "ranking", "compare", "mom", "contribution"] | None = None
    prompt_version: Token
    state_version: Token
    semantic_version: Token
    model_name: Token
    model_call_count: int = Field(ge=0, le=2)
    query_count: int = Field(ge=0, le=4)
    node_path: tuple[Node, ...] = Field(max_length=20)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    error_code: Literal["model_error", "plan_validation_error", "execution_error", "conversation_error"] | None = None
    completeness: bool
    evidence: dict[Evidence, Id]


def make_record(view, thread_id, run_id):
    from eda.conversation.models import PROMPT_VERSION, STATE_VERSION
    from eda.metrics.definitions import SEMANTIC
    meta = view.technical
    return AuditRecord(run_id=run_id, request_id=meta.get("request_id", run_id),
        created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        thread_digest=sha256(thread_id.encode("utf-8")).hexdigest(), status=view.status,
        analysis_operation=view.plan.get("operation"), prompt_version=PROMPT_VERSION,
        state_version=STATE_VERSION, semantic_version=SEMANTIC.schema_version,
        model_name=meta.get("model_name", "unavailable"), model_call_count=meta.get("model_call_count", 0),
        query_count=meta.get("query_count", 0), node_path=meta.get("node_path", ()),
        elapsed_ms=float(meta.get("elapsed_ms", 0.0)),
        error_code=view.status if view.status.endswith("error") else None,
        completeness=view.completeness.get("is_complete_population", False), evidence=view.evidence)
