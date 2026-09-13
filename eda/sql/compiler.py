"""Compile a validated AnalysisPlan into parameterized SQL.

Identifiers come only from the already-approved semantic layer. Filter values
are bound parameters. This module never executes SQL.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from eda.metrics.definitions import BREAKDOWN_DIMENSIONS, METRIC_REGISTRY, SEMANTIC
from eda.plan.models import AnalysisPlan, parse_analysis_plan

METRIC_VALUE_ALIAS = "metric_value"
DIMENSION_VALUE_ALIAS = "dimension_value"


class CompiledQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str
    params: dict[str, Any]
    semantic_version: str
    metric_id: str
    operation: str
    dimension_id: str | None
    display_metric_name_zh: str


def compile_plan(plan: AnalysisPlan) -> CompiledQuery:
    """Return stable parameterized SQL for one approved plan."""
    plan = parse_analysis_plan(plan)
    definition = METRIC_REGISTRY[plan.metric_id]
    view = definition.base_view
    expression = definition.sql_expression
    params: dict[str, Any] = {
        "start_date": plan.start_date,
        "end_date": plan.end_date,
    }
    where_parts = ["order_date >= :start_date", "order_date <= :end_date"]

    for index, clause in enumerate(plan.filters):
        column = SEMANTIC.dimension(clause.dimension_id).source_field
        values = clause.values()
        if clause.op == "eq":
            key = f"filter_{index}"
            where_parts.append(f"{column} = :{key}")
            params[key] = values[0]
        else:
            keys = []
            for offset, value in enumerate(values):
                key = f"filter_{index}_{offset}"
                keys.append(f":{key}")
                params[key] = value
            where_parts.append(f"{column} IN ({', '.join(keys)})")

    where_sql = " AND ".join(where_parts)
    if plan.operation == "total":
        sql = (
            f"SELECT\n    {expression} AS {METRIC_VALUE_ALIAS}\n"
            f"FROM {view}\n"
            f"WHERE {where_sql}"
        )
        display_name = definition.name_zh
    else:
        assert plan.dimension_id is not None
        assert plan.order_by is not None
        assert plan.sort_direction is not None
        dimension = SEMANTIC.dimension(plan.dimension_id)
        column = BREAKDOWN_DIMENSIONS[plan.dimension_id]
        direction = "DESC" if plan.sort_direction == "desc" else "ASC"
        if plan.order_by == "metric_value":
            order_sql = (
                f"{METRIC_VALUE_ALIAS} {direction} NULLS LAST, "
                f"{DIMENSION_VALUE_ALIAS} ASC"
            )
        else:
            order_sql = (
                f"{DIMENSION_VALUE_ALIAS} {direction} NULLS LAST, "
                f"{METRIC_VALUE_ALIAS} DESC"
            )
        sql = (
            f"SELECT\n"
            f"    {column} AS {DIMENSION_VALUE_ALIAS},\n"
            f"    {expression} AS {METRIC_VALUE_ALIAS}\n"
            f"FROM {view}\n"
            f"WHERE {where_sql}\n"
            f"GROUP BY {column}\n"
            f"ORDER BY {order_sql}"
        )
        if plan.top_n is not None:
            sql += "\nLIMIT :top_n"
            params["top_n"] = int(plan.top_n)
        display_name = (
            dimension.aov_display_name_zh
            if plan.metric_id == "aov_cents"
            else definition.name_zh
        )

    return CompiledQuery(
        sql=sql,
        params=params,
        semantic_version=SEMANTIC.schema_version,
        metric_id=plan.metric_id,
        operation=plan.operation,
        dimension_id=plan.dimension_id,
        display_metric_name_zh=display_name,
    )
