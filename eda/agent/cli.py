"""JSON-only single-turn CLI. Fake by default; real HTTPS requires --provider real."""

import argparse
import json
from pathlib import Path

from eda.agent.fake import FakePlannerModel
from eda.agent.service import run_question
from eda.config import get_settings


class InputError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("invalid CLI arguments")


def main(argv: list[str] | None = None) -> int:
    try:
        settings = get_settings()
        parser = Parser(description="单轮经营规划（默认离线 fake）")
        parser.add_argument("question")
        parser.add_argument("--db", type=Path, default=settings.business_db_path)
        parser.add_argument("--reference-date", default=settings.analysis_reference_date)
        parser.add_argument("--provider", choices=("fake", "real"), default="fake")
        args = parser.parse_args(argv)
        if args.provider == "real":
            from eda.agent.real import RealPlannerModel

            model = RealPlannerModel()
        else:
            model = FakePlannerModel()
        result = run_question(args.question, args.db, model=model, reference_date=args.reference_date)
        # Success contains the requested validated plan. It is never an audit log.
        print(result.model_dump_json(exclude={name for name in type(result).model_fields if getattr(result, name) is None}))
        return {"success": 0, "clarification_required": 2, "refused": 3,
                "model_error": 4, "plan_validation_error": 5, "execution_error": 6}[result.status]
    except (InputError, ValueError, OSError):
        print(json.dumps({"status": "plan_validation_error", "error_code": "invalid_input",
                          "error_message": "命令行参数或本地配置无效。"}, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
