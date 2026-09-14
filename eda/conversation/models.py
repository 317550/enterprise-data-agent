"""Bounded persistent business state; patches distinguish omission/set/clear."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eda.agent.dates import strict_date
from eda.agent.models import DecisionError, MAX_OUTPUT_CHARS, REFUSALS, _unique_object
from eda.metrics.definitions import SEMANTIC
from eda.plan.models import FilterClause, parse_analysis_plan

STATE_VERSION = "conversation-v1"
PROMPT_VERSION = "conversation-plan-v1"
PlanField = Literal["metric_id", "operation", "dimension_id", "start_date", "end_date", "top_n", "order_by", "sort_direction", "filters"]
Intent = Literal["new", "refine", "clarify_reply"]
Missing = Literal["intent", "history", "metric_id", "dates", "dimension_id", "filters", "top_n"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Values(StrictModel):
    """Independent partial draft, never passed to the executor without validation."""
    metric_id: str | None = Field(default=None, max_length=80)
    operation: Literal["total", "breakdown"] | None = None
    dimension_id: str | None = Field(default=None, max_length=80)
    start_date: str | None = None
    end_date: str | None = None
    top_n: int | None = Field(default=None, strict=True, ge=1, le=100)
    order_by: Literal["metric_value", "dimension_value"] | None = None
    sort_direction: Literal["asc", "desc"] | None = None
    filters: tuple[FilterClause, ...] = Field(default=(), max_length=6)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, data):
        if isinstance(data, dict) and any(value is None for value in data.values()):
            raise ValueError("omit for inheritance; clear explicitly, never null")
        return data

    @field_validator("start_date", "end_date")
    @classmethod
    def date_format(cls, value):
        if value is not None:
            strict_date(value)
        return value

    @field_validator("metric_id")
    @classmethod
    def metric(cls, value):
        if value is not None and value not in SEMANTIC.metric_ids:
            raise ValueError("unapproved metric")
        return value

    @field_validator("dimension_id")
    @classmethod
    def dimension(cls, value):
        if value is not None and value not in SEMANTIC.dimension_ids:
            raise ValueError("unapproved dimension")
        return value

    @field_validator("filters")
    @classmethod
    def approved_filters(cls, value):
        seen = set()
        for clause in value:
            dimension = SEMANTIC.dimension(clause.dimension_id)
            if dimension.id not in {"region", "category"} or dimension.id in seen:
                raise ValueError("unapproved or duplicate filter")
            if any(item not in dimension.allowed_values for item in clause.values()):
                raise ValueError("unapproved filter value")
            seen.add(dimension.id)
        return value


class Patch(StrictModel):
    set: Values = Field(default_factory=Values)
    clear: tuple[PlanField, ...] = Field(default=(), max_length=9)
    clear_filters: tuple[Literal["region", "category"], ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def disjoint(self):
        provided = self.set.model_fields_set
        if set(self.clear) & provided or len(set(self.clear)) != len(self.clear):
            raise ValueError("conflicting patch")
        if self.clear_filters and ("filters" in self.clear or "filters" in provided):
            raise ValueError("conflicting filter patch")
        if len(set(self.clear_filters)) != len(self.clear_filters):
            raise ValueError("duplicate filter clear")
        if "dimension_id" in self.clear and provided & {"dimension_id", "top_n", "order_by", "sort_direction"}:
            raise ValueError("cleared breakdown cannot carry ranking")
        if "dimension_id" in self.clear and self.set.operation == "breakdown":
            raise ValueError("conflicting operation")
        return self


class TurnDecision(StrictModel):
    status: Literal["apply", "clarify", "refuse"]
    intent: Intent | None = None
    patch: Patch = Field(default_factory=Patch)
    missing: tuple[Missing, ...] = Field(default=(), max_length=7)
    refusal_category: Literal["write_request", "unauthorized_request", "unsupported_analysis"] | None = None

    @model_validator(mode="before")
    @classmethod
    def exclusive_fields(cls, data):
        if not isinstance(data, dict):
            raise ValueError("decision must be an object")
        allowed = {"apply": {"status", "intent", "patch"},
                   "clarify": {"status", "intent", "patch", "missing"},
                   "refuse": {"status", "refusal_category"}}.get(data.get("status"))
        if allowed is None or set(data) - allowed or any(value is None for value in data.values()):
            raise ValueError("fields do not match status")
        return data

    @model_validator(mode="after")
    def combination(self):
        if len(set(self.missing)) != len(self.missing):
            raise ValueError("duplicate missing field")
        if self.status == "apply" and (self.intent is None or self.missing or self.refusal_category):
            raise ValueError("apply requires intent and no terminal fields")
        if self.status == "clarify" and (not self.missing or self.refusal_category):
            raise ValueError("clarify requires missing fields")
        if self.status == "refuse" and (self.refusal_category not in REFUSALS or self.intent or self.missing or self.patch != Patch()):
            raise ValueError("refuse cannot carry a candidate")
        return self


class Draft(StrictModel):
    values: Values
    missing: tuple[Missing, ...] = Field(min_length=1, max_length=7)
    intent: Intent | None = None


class Session(StrictModel):
    state_version: Literal["conversation-v1"] = STATE_VERSION
    semantic_version: str = SEMANTIC.schema_version
    reference_date: str
    confirmed: dict | None = None
    pending: Draft | None = None
    turn_count: int = Field(default=0, strict=True, ge=0, le=2147483647)
    turn_status: Literal["completed", "in_progress"] = "completed"

    @field_validator("reference_date")
    @classmethod
    def reference(cls, value):
        strict_date(value)
        return value

    @field_validator("semantic_version")
    @classmethod
    def semantic(cls, value):
        if value != SEMANTIC.schema_version:
            raise ValueError("incompatible semantic version")
        return value

    @field_validator("confirmed")
    @classmethod
    def confirmed_plan(cls, value):
        if value is not None:
            return parse_analysis_plan(value).model_dump(exclude_none=True)
        return value


def parse_turn(raw: object) -> TurnDecision:
    try:
        if isinstance(raw, BaseModel):
            raw = raw.model_dump(exclude_none=True, exclude_unset=True)
        if isinstance(raw, str):
            if len(raw) > MAX_OUTPUT_CHARS:
                raise DecisionError("invalid_structure")
            raw = json.loads(raw, object_pairs_hook=_unique_object)
        return TurnDecision.model_validate(raw)
    except DecisionError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise DecisionError("invalid_structure") from None
