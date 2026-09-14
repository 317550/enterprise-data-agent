import pytest

from eda.agent.dates import resolve_dates
from eda.agent.models import DecisionError
from eda.conversation.merge import merge_decision
from eda.conversation.models import Draft, Session, TurnDecision, Values, parse_turn


def session():
    return Session(reference_date="2024-12-31", confirmed={
        "metric_id": "effective_order_gmv_cents", "operation": "breakdown", "dimension_id": "region",
        "start_date": "2024-01-01", "end_date": "2024-12-31", "top_n": 2,
        "filters": [{"dimension_id": "region", "op": "eq", "value": "华东"}],
    })


def merge(patch, question="改成订单数", state=None, intent="refine"):
    return merge_decision(state or session(), parse_turn({"status": "apply", "intent": intent, "patch": patch}),
                          resolve_dates(question, "2024-12-31"))


def test_inherit_and_replace():
    result = merge({"set": {"metric_id": "valid_order_count"}})
    assert result.plan.start_date == "2024-01-01"
    assert result.plan.filters[0].value == "华东"
    assert result.changed == ("metric_id",)
    assert "filters.region" in result.inherited


@pytest.mark.parametrize("patch,removed", [({"clear_filters": ["region"]}, "filters.region"),
                                           ({"clear": ["filters"]}, "filters.region"),
                                           ({"clear": ["top_n"]}, "top_n"),
                                           ({"clear": ["dimension_id"]}, "dimension_id")])
def test_explicit_clear(patch, removed):
    result = merge(patch)
    assert removed in result.cleared
    if removed == "dimension_id":
        assert result.plan.operation == "total" and result.plan.top_n is None


@pytest.mark.parametrize("patch", [{"set": {"top_n": None}}, {"set": {"filters": None}},
                                   {"set": {"dimension_id": None}}, {"set": {"sql": "SELECT 1"}},
                                   {"set": {"top_n": 1}, "clear": ["top_n"]}])
def test_null_and_executable_fields_cannot_be_changes(patch):
    with pytest.raises(DecisionError):
        parse_turn({"status": "apply", "intent": "refine", "patch": patch})


def test_invalid_dates_do_not_fall_back():
    result = merge({}, "改为2024-02-30")
    assert result.plan is None
    assert "dates" in result.draft.missing
    assert result.draft.values.start_date is None


def test_no_history_and_new_do_not_inherit():
    result = merge({}, state=Session(reference_date="2024-12-31"))
    assert "history" in result.draft.missing
    result = merge({"set": {"metric_id": "valid_order_count"}}, intent="new")
    assert result.draft.values.filters == ()
    assert result.draft.values.start_date is None


def test_clarification_reply_continues_independent_draft():
    state = Session(reference_date="2024-12-31", pending=Draft(
        values=Values(metric_id="valid_order_count", operation="total"), missing=("dates",), intent="new"))
    result = merge({}, "2024年", state, "clarify_reply")
    assert result.plan.metric_id == "valid_order_count"
    assert result.plan.start_date == "2024-01-01"


@pytest.mark.parametrize("patch", [
    {"clear_filters": ["region", "region"]},
    {"clear": ["dimension_id"], "set": {"operation": "breakdown"}},
    {"clear": ["dimension_id"], "set": {"top_n": 2}},
    {"set": {"filters": [{"dimension_id": "region", "op": "eq", "value": "未知"}]}},
    {"set": {"filters": [{"dimension_id": "arbitrary", "op": "eq", "value": "x"}]}},
])
def test_invalid_partial_patches(patch):
    with pytest.raises(DecisionError):
        parse_turn({"status": "apply", "intent": "refine", "patch": patch})


def test_filter_set_replaces_entire_list():
    result = merge({"set": {"filters": [{"dimension_id": "category", "op": "eq", "value": "手机数码"}]}})
    assert [f.dimension_id for f in result.plan.filters] == ["category"]
    assert "filters.region" in result.cleared


def test_cleared_required_field_becomes_clarification():
    result = merge({"clear": ["metric_id"]})
    assert result.plan is None and "metric_id" in result.draft.missing


def test_ambiguous_intent_cannot_execute_on_reply():
    state = Session(reference_date="2024-12-31", pending=Draft(
        values=Values(metric_id="valid_order_count"), missing=("intent",)))
    result = merge({}, "2024年", state, "clarify_reply")
    assert "intent" in result.draft.missing


def test_duplicate_json_and_refusal_rejected_by_merge():
    with pytest.raises(DecisionError):
        parse_turn('{"status":"apply","status":"refuse"}')
    with pytest.raises(ValueError):
        merge_decision(session(), parse_turn({"status": "refuse", "refusal_category": "write_request"}),
                       resolve_dates("2024年", "2024-12-31"))


@pytest.mark.parametrize("payload", [
    {"status": "apply", "intent": "new", "missing": []},
    {"status": "apply", "intent": "new", "refusal_category": None},
    {"status": "refuse", "refusal_category": "write_request", "patch": {}},
    {"status": "clarify", "missing": ["intent", "intent"]},
])
def test_status_fields_are_exclusive(payload):
    with pytest.raises(DecisionError):
        parse_turn(payload)
