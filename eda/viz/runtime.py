"""Fixed local UI paths and one explicit turn; no alternate query pipeline."""
import os
from time import perf_counter
from eda.config import PROJECT_ROOT
from eda.conversation.fake import FakeConversationModel
from eda.conversation.real import RealConversationModel
from eda.conversation.service import ConversationService
from eda.conversation.models import PROMPT_VERSION, STATE_VERSION
from eda.metrics.definitions import SEMANTIC
from eda.viz.models import ViewModel
from eda.viz.transform import to_view, MESSAGES
from eda.audit.models import make_record
from eda.audit.store import write_record, AUDIT_WARNING

BUSINESS = PROJECT_ROOT / "data/fixture.db"
CHECKPOINT = PROJECT_ROOT / "data/ui-checkpoints.db"
AUDIT = PROJECT_ROOT / "data/ui-audit.jsonl"


def service(provider, reference_date):
    return ConversationService(BUSINESS, CHECKPOINT, model=FakeConversationModel() if provider == "fake" else RealConversationModel(), reference_date=reference_date)


def submit(thread_id, question, provider, reference_date, new_topic, run_id):
    started = perf_counter()
    try:
        if provider not in {"fake", "real"}:
            raise ValueError("invalid_provider")
        if provider == "real" and not os.environ.get("DEEPSEEK_API_KEY"):
            view = ViewModel(status="model_error", message=MESSAGES["model_error"])
        else:
            response = service(provider, reference_date).run(thread_id, question, new_topic=new_topic)
            view = to_view(response, (perf_counter() - started) * 1000)
    except Exception:
        view = ViewModel(status="conversation_error", message=MESSAGES["conversation_error"])
    # Failures before service entry still have a traceable, safe UI identity.
    meta = {"request_id": run_id, "prompt_version": PROMPT_VERSION,
            "state_version": STATE_VERSION, "semantic_version": SEMANTIC.schema_version,
            "model_name": "unavailable", "model_call_count": 0, "query_count": 0,
            "node_path": (), **view.technical,
            "elapsed_ms": (perf_counter() - started) * 1000}
    view = view.model_copy(update={"technical": meta})
    try:
        warning = write_record(AUDIT, make_record(view, thread_id, run_id), business_path=BUSINESS, checkpoint_path=CHECKPOINT)
    except Exception:
        warning = AUDIT_WARNING
    view = view.model_copy(update={"technical": {**meta, "elapsed_ms": (perf_counter() - started) * 1000}})
    return view, warning
