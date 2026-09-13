"""Application settings, loaded from environment / .env.

Nothing in this project hard-codes an API key, a base URL or a model name --
they are all read from here, so switching models never means editing logic.

The API key is a :class:`~pydantic.SecretStr`; printing the settings object or
logging it shows ``**********`` instead of the key.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- DeepSeek (unused until stage 3; stage 1 never calls an LLM) ---------
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_output_tokens: int = Field(default=2048, gt=0)

    # --- Databases ----------------------------------------------------------
    #: Business data. Opened read-only everywhere except the build script.
    business_db_path: Path = PROJECT_ROOT / "data" / "business.db"
    #: Tiny hand-checkable dataset, handy for manual review and demos.
    fixture_db_path: Path = PROJECT_ROOT / "data" / "fixture.db"
    #: LangGraph checkpoints (stage 4). Written by the app; never exposed to the
    #: model and never reachable from generated SQL.
    checkpoint_db_path: Path = PROJECT_ROOT / "data" / "checkpoints.db"

    # --- Reproducible demo data --------------------------------------------
    demo_seed: int = 20240101
    demo_start_date: str = "2024-01-01"
    demo_end_date: str = "2024-12-31"

    # --- Read-only SQL limits (enforced from stage 2) -----------------------
    # These are in-process budgets, not a hard OS sandbox or hard realtime.
    sql_max_rows: int = Field(default=1000, gt=0)
    sql_timeout_seconds: float = Field(default=10.0, gt=0)
    sql_max_sql_chars: int = Field(default=8000, gt=0)
    sql_max_result_bytes: int = Field(default=256000, gt=0)
    #: Byte budget for ``SQLITE_LIMIT_LENGTH`` (strings/blobs), not a character count.
    sql_max_value_bytes: int = Field(default=4096, gt=0)

    # --- Test switches ------------------------------------------------------
    #: Must stay False in CI and in the default test run: the automated suite
    #: never makes a billable external call.
    enable_live_llm_tests: bool = False

    @field_validator("business_db_path", "fixture_db_path", "checkpoint_db_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("demo_start_date", "demo_end_date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        import datetime as dt

        dt.date.fromisoformat(value)
        return value

    def redacted_summary(self) -> dict[str, object]:
        """Safe to print, log or show in the UI."""
        return {
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "deepseek_api_key": "set" if self.deepseek_api_key else "missing",
            "business_db_path": str(self.business_db_path),
            "checkpoint_db_path": str(self.checkpoint_db_path),
            "demo_seed": self.demo_seed,
            "demo_window": f"{self.demo_start_date}..{self.demo_end_date}",
            "sql_max_rows": self.sql_max_rows,
            "sql_timeout_seconds": self.sql_timeout_seconds,
            "sql_max_sql_chars": self.sql_max_sql_chars,
            "sql_max_result_bytes": self.sql_max_result_bytes,
            "sql_max_value_bytes": self.sql_max_value_bytes,
            "enable_live_llm_tests": self.enable_live_llm_tests,
        }


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
