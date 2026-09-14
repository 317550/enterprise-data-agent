"""Completed-turn recovery API; never replay requests or result rows from disk."""

from pathlib import Path

from eda.agent.dates import strict_date
from eda.agent.planner import PlannerModel
from eda.config import get_settings
from eda.conversation.checkpoint import (
    ConversationError, checkpoint_path, open_checkpointer, thread_lock, validate_thread,
)
from eda.conversation.graph import TurnContext, TurnResult, build_graph
from eda.conversation.models import Session
from eda.sql.executor import ExecutionLimits


class ConversationService:
    def __init__(self, db_path: str | Path, checkpoint_db_path: str | Path, *, model: PlannerModel,
                 reference_date: str | None = None, limits: ExecutionLimits | None = None):
        self.db_path = Path(db_path).resolve()
        self.checkpoint_db_path = checkpoint_path(checkpoint_db_path, self.db_path)
        self.model = model
        self.reference_date = reference_date
        if reference_date is not None:
            strict_date(reference_date)
        self.limits = limits

    def _session(self, snapshot) -> Session:
        if not snapshot.values:
            return Session(reference_date=self.reference_date or get_settings().analysis_reference_date)
        try:
            raw = snapshot.values["session"]
            if not {"state_version", "semantic_version", "reference_date"} <= raw.keys():
                raise ValueError("missing version")
            session = Session.model_validate(raw)
        except Exception:
            raise ConversationError("incompatible_checkpoint") from None
        if self.reference_date is not None and self.reference_date != session.reference_date:
            raise ConversationError("reference_date_mismatch")
        if session.turn_count >= 2147483647:
            raise ConversationError("thread_turn_limit")
        return session

    def run(self, thread_id: str, question: str, *, new_topic: bool = False) -> TurnResult:
        validate_thread(thread_id)
        if not isinstance(question, str) or not question.strip() or len(question) > 2048:
            raise ConversationError("invalid_input")
        # Recheck aliases/foreign schemas on every open, not just construction.
        path = checkpoint_path(self.checkpoint_db_path, self.db_path)
        with thread_lock(path, thread_id), open_checkpointer(path) as saver:
            graph = build_graph(saver)
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 10}
            session = self._session(graph.get_state(config))
            ctx = TurnContext(question, self.db_path, self.model, self.limits, new_topic=new_topic)
            # Always submit a new input. Never invoke(None) to replay an
            # unfinished task: its original Runtime.context is intentionally gone.
            graph.invoke({"session": session.model_dump(exclude_none=True)}, config,
                         context=ctx, durability="sync")
            if ctx.result is None:
                raise ConversationError("turn_incomplete")
            return ctx.result

    def inspect(self, thread_id: str) -> Session:
        validate_thread(thread_id)
        path = checkpoint_path(self.checkpoint_db_path, self.db_path)
        with thread_lock(path, thread_id), open_checkpointer(path) as saver:
            return self._session(build_graph(saver).get_state({"configurable": {"thread_id": thread_id}}))
