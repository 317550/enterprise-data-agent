"""Settings hygiene and metric-definition metadata."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from eda.config import PROJECT_ROOT, Settings
from eda.domain.enums import EXCLUDED_FROM_REVENUE_STATUSES, REVENUE_STATUSES
from eda.metrics import (
    GMV_METRIC_ID,
    METRIC_REGISTRY,
    MetricFilters,
    build_core_metrics_sql,
    resolve_metric,
)
from eda.metrics.definitions import build_breakdown_sql


def test_defaults_do_not_require_a_dotenv_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model
    assert settings.enable_live_llm_tests is False
    assert settings.demo_start_date == "2024-01-01"


def test_database_paths_are_absolute_and_separate() -> None:
    settings = Settings(_env_file=None)
    for path in (
        settings.business_db_path,
        settings.fixture_db_path,
        settings.checkpoint_db_path,
    ):
        assert path.is_absolute()
        assert path.is_relative_to(PROJECT_ROOT)
    assert settings.business_db_path != settings.checkpoint_db_path


def test_api_key_is_never_printed() -> None:
    settings = Settings(_env_file=None, deepseek_api_key=SecretStr("sk-super-secret-value"))
    assert "sk-super-secret-value" not in repr(settings)
    assert "sk-super-secret-value" not in str(settings)
    assert "sk-super-secret-value" not in str(settings.redacted_summary())
    assert settings.redacted_summary()["deepseek_api_key"] == "set"
    # The value is still reachable on purpose, but only explicitly.
    assert settings.deepseek_api_key.get_secret_value() == "sk-super-secret-value"


def test_invalid_demo_date_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, demo_start_date="2024-13-01")


def test_sql_value_byte_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, sql_max_value_bytes=0)
    settings = Settings(_env_file=None)
    assert settings.sql_max_value_bytes == 4096


def test_revenue_status_classification_is_exhaustive_and_disjoint() -> None:
    assert set(REVENUE_STATUSES) == {"paid", "completed"}
    assert set(REVENUE_STATUSES) & set(EXCLUDED_FROM_REVENUE_STATUSES) == set()


def test_every_metric_documents_its_definition() -> None:
    for key, definition in METRIC_REGISTRY.items():
        assert definition.key == key
        assert definition.name_zh and definition.definition_zh
        assert definition.unit and definition.grain and definition.base_view


def test_revenue_definition_is_labelled_as_simplified_not_net() -> None:
    """Stage 1.1 renamed this metric; the stage 1 id still resolves to it."""
    revenue = resolve_metric("revenue_cents")
    assert revenue.key == GMV_METRIC_ID
    assert "简化" in revenue.name_zh or "简化" in " ".join(revenue.caveats)
    assert any("净收入" in caveat for caveat in revenue.caveats)


def test_gmv_metric_is_named_effective_order_gmv() -> None:
    """口径统一后的名称与说明（阶段 1.1 要求）。"""
    gmv = METRIC_REGISTRY[GMV_METRIC_ID]
    assert gmv.name_zh == "有效订单成交额（简化口径）"
    assert "有效订单成交额" in gmv.synonyms
    caveats = " ".join(gmv.caveats)
    assert "部分退款" in caveats
    assert "gross revenue" in caveats, "必须明确否认标准 gross revenue 口径"
    assert "闭区间" in caveats, "日期区间的包含规则必须写明"


def test_order_count_definition_insists_on_distinct() -> None:
    assert "COUNT(DISTINCT order_id)" in METRIC_REGISTRY["valid_order_count"].sql_expression


def test_core_metrics_sql_binds_parameters_instead_of_formatting_them() -> None:
    filters = MetricFilters(start_date="2024-01-01", end_date="2024-03-31", region="华东")
    sql, params = build_core_metrics_sql(filters)

    assert "2024-01-01" not in sql and "华东" not in sql
    assert ":start_date" in sql and ":region" in sql
    assert params == {
        "start_date": "2024-01-01",
        "end_date": "2024-03-31",
        "region": "华东",
        "category": None,
    }


def test_breakdown_limit_is_bound_not_interpolated() -> None:
    filters = MetricFilters(start_date="2024-01-01", end_date="2024-12-31")
    sql, params = build_breakdown_sql("region", filters, limit=3)
    assert "LIMIT :row_limit" in sql
    assert params["row_limit"] == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [("region", "火星"), ("category", "未知类别")],
)
def test_filters_reject_unknown_dimension_values(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        MetricFilters(start_date="2024-01-01", end_date="2024-12-31", **{field: value})


def test_filters_reject_reversed_range() -> None:
    with pytest.raises(ValueError, match="start_date must not be after end_date"):
        MetricFilters(start_date="2024-06-01", end_date="2024-01-01")
