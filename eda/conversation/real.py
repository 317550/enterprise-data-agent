"""Explicit real multi-turn adapter; shares transport, never PlannerDecision."""

from eda.agent.models import DecisionError
from eda.agent.planner import ModelFailure
from eda.agent.real import RealPlannerModel
from eda.conversation.context import planning_context
from eda.conversation.models import Session, parse_turn


class RealConversationModel(RealPlannerModel):
    def plan(self, question: str, context: dict) -> object:
        # Rebuild the prompt from the approved business state. Never forward
        # arbitrary context keys, caller rules, message history or query results.
        try:
            session = Session(reference_date=context["reference_date"],
                              confirmed=context.get("confirmed"), pending=context.get("pending"))
            repair = "invalid_plan" if context.get("repair_error") == "invalid_plan" else None
            approved = planning_context(session, repair)
        except Exception:
            raise ModelFailure("model_configuration_error") from None
        raw = self._request_json(question, approved)
        try:
            return parse_turn(raw)
        except DecisionError:
            # Invalid output is a validation outcome, not a transport retry.
            # The graph's merge -> plan edge owns the single repair budget.
            return ""
