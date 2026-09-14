"""One turn per invocation, fake by default; reuse --thread after restart."""

import json
from pathlib import Path

from eda.agent.cli import InputError, Parser
from eda.config import get_settings
from eda.conversation.checkpoint import ConversationError
from eda.conversation.fake import FakeConversationModel
from eda.conversation.service import ConversationService


def main(argv: list[str] | None = None) -> int:
    try:
        settings = get_settings()
        parser = Parser(description="阶段 4A 多轮查询（默认 fake）；相同 thread 续接，忙时拒绝")
        parser.add_argument("question")
        parser.add_argument("--thread", required=True)
        parser.add_argument("--db", type=Path, default=settings.business_db_path)
        parser.add_argument("--checkpoint-db", type=Path, default=settings.checkpoint_db_path)
        parser.add_argument("--reference-date", default=None)
        parser.add_argument("--provider", choices=("fake", "real"), default="fake")
        parser.add_argument("--new-topic", action="store_true", help="本轮从空业务计划开始，由代码强制 new 意图")
        args = parser.parse_args(argv)
        if args.provider == "real":
            from eda.conversation.real import RealConversationModel

            model = RealConversationModel()
        else:
            model = FakeConversationModel()
        service = ConversationService(args.db, args.checkpoint_db, model=model,
                                      reference_date=args.reference_date)
        result = service.run(args.thread, args.question, new_topic=args.new_topic)
        print(result.model_dump_json(exclude_none=True))
        return {"success": 0, "clarification_required": 2, "refused": 3,
                "model_error": 4, "plan_validation_error": 5, "execution_error": 6}[result.status]
    except ConversationError as exc:
        print(json.dumps({"status": "conversation_error", "error_code": exc.code}))
        return 7
    except (InputError, ValueError, OSError):
        print(json.dumps({"status": "conversation_error", "error_code": "invalid_input"}))
        return 7
    except Exception:
        print(json.dumps({"status": "conversation_error", "error_code": "checkpoint_unavailable"}))
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
