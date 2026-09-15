"""Project existing safe results; never recompute business metrics."""
from eda.conversation.graph import TurnResult
from eda.metrics.definitions import SEMANTIC
from eda.viz.models import ChartSpec, ViewModel

MESSAGES = {"success": "分析完成", "clarification_required": "请补充明确的指标、期间或维度。",
            "refused": "请求不在支持范围内；无法判断原因。", "model_error": "模型不可用或配置不完整。",
            "plan_validation_error": "分析计划未通过校验。", "execution_error": "查询未完成。",
            "conversation_error": "会话不可用，请检查会话编号与参考日期。"}
OPERATIONS = {"total": "合计", "breakdown": "分类拆分", "ranking": "排名", "compare": "期间比较", "mom": "月度环比", "contribution": "变化贡献"}


def to_view(result, elapsed_ms=0.0):
    if not isinstance(result, TurnResult):
        return ViewModel(status="conversation_error", message=MESSAGES["conversation_error"])
    result = TurnResult.model_validate(result)
    metadata = {k: getattr(result, k) for k in ("request_id", "prompt_version", "state_version", "semantic_version", "model_name", "model_call_count", "query_count", "node_path")}
    metadata["elapsed_ms"] = elapsed_ms
    if result.status != "success":
        return ViewModel(status=result.status, message=MESSAGES[result.status], technical=metadata)
    plan = result.plan.model_dump(mode="json", exclude_none=True)
    metric = SEMANTIC.metric(result.plan.metric_id)
    single = result.analysis_result
    unit = single.unit if single else metric.unit_zh
    op = result.analysis_operation
    completeness = (single.completeness if single else result.completeness).model_dump()
    completeness.setdefault("calendar_period_complete", None)
    completeness.setdefault("data_coverage_verified", False)
    warnings = ["未验证数据库期间内部无缺失", "无法判断原因"]
    if result.warnings or (single and single.warnings):
        warnings.append("服务返回口径提示，请核对计划、实际期间及完整性标记。")
    if completeness.get("truncated") or not completeness["is_complete_population"]:
        warnings.append("结果不能代表完整总体。")
    metadata.update({k: getattr(result, k) for k in ("inherited", "changed", "cleared")})
    rows, chart_rows, kpis, evidence, hidden = (), (), {}, {}, {}
    chart = None
    complete = completeness["is_complete_population"] and not completeness.get("truncated", False)
    if single:
        rows = tuple(r.model_dump(mode="json") for r in single.rows)
        evidence = {"single_result": single.query_id}
        metadata["sql"] = single.execution.sql
        if op == "total" and rows:
            kpis = {"metric_value": rows[0]["metric_value"]}
        elif rows and complete:
            chart_rows = tuple({"category": r["dimension_value"], "value": r["metric_value"]} for r in rows)
            chart = ChartSpec(chart_type="bar", title="分类结果", x_field="category", y_field="value", x_kind="category", y_unit=unit, sort_direction=plan.get("sort_direction", "none"), source_kind="single", completeness=True)
    else:
        evidence = {e.evidence_id: e.query_id for e in result.evidence}
        metadata["execution_plan"] = [e.model_dump(mode="json") for e in result.evidence]
        kpis = result.comparison.model_dump(mode="json") if result.comparison else {}
        rows = (kpis,) if kpis else ()
        if result.contribution:
            rows = tuple(r.model_dump(mode="json") for r in result.contribution.rows)
            hidden = {"dimension_id": result.contribution.dimension_id, "hidden_dimension_count": result.contribution.hidden_dimension_count, "hidden_net_change": str(result.contribution.hidden_net_change)}
            warnings.append("单维度算术分解；负贡献或超过100%的贡献率表示存在抵消效应。")
            if rows and complete:
                chart_rows = tuple({"category": r["dimension_value"], "change": r["absolute_change"]} for r in rows)
                chart = ChartSpec(chart_type="bar", title="单维度变化贡献", x_field="category", y_field="change", x_kind="category", y_unit=unit, sort_direction=plan.get("sort_direction", "desc"), source_kind="contribution", completeness=True)
        elif kpis and complete:
            chart_rows = tuple(sorted(({"period": getattr(result, p + "_period").start_date, "value": kpis[p + "_value"]} for p in ("baseline", "current")), key=lambda r: r["period"]))
            chart = ChartSpec(chart_type="line" if op == "mom" else "bar", title="月度环比（仅两个期间）" if op == "mom" else "期间比较", x_field="period", y_field="value", x_kind="temporal" if op == "mom" else "category", y_unit=unit, sort_direction="asc", source_kind="comparison", completeness=True)
    display_operation = "排名" if single and plan.get("top_n") else OPERATIONS.get(op, op)
    return ViewModel(status="success", message=MESSAGES["success"], finding_type="decomposition" if result.contribution else "observation", metric=single.display_metric_name_zh if single else metric.name_zh, operation=display_operation, unit=unit, plan=plan, kpis=kpis, rows=rows, chart=chart, chart_rows=chart_rows, completeness=completeness, warnings=tuple(warnings), evidence=evidence, technical=metadata, hidden=hidden)
