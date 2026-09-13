"""Thin CLI: read an AnalysisPlan JSON file and print the structured result.

    python -m eda.query.cli --plan examples\\plans\\fixture_gmv_total.json --db data\\fixture.db

Business logic lives in :mod:`eda.query.service`, not here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eda.config import get_settings
from eda.db import DatabaseError
from eda.query.errors import QueryError
from eda.query.service import run_analysis_plan

_SAFE_DB_UNAVAILABLE = "business database was not found or could not be opened"


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m eda.query.cli",
        description="Run one AnalysisPlan JSON through the stage 2 query pipeline.",
    )
    parser.add_argument("--plan", type=Path, required=True, help="path to AnalysisPlan JSON")
    parser.add_argument("--db", type=Path, default=settings.business_db_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.plan.read_text(encoding="utf-8"))
    except OSError:
        print(json.dumps({"status": "error", "error_code": "invalid_params", "error_message": "plan file could not be read"}, ensure_ascii=False))
        return 1
    except (json.JSONDecodeError, UnicodeError):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": "invalid_plan",
                    "error_message": "plan is not valid UTF-8 JSON",
                },
                ensure_ascii=False,
            )
        )
        return 1
    try:
        result = run_analysis_plan(payload, args.db)
    except QueryError as exc:
        print(
            json.dumps(
                {"status": "error", "error_code": exc.code, "error_message": exc.message},
                ensure_ascii=False,
            )
        )
        return 1
    except DatabaseError:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": "db_error",
                    "error_message": _SAFE_DB_UNAVAILABLE,
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
