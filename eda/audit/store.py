"""Separate bounded JSONL audit file with nonblocking OS lock and idempotency."""
from pathlib import Path
import json
from eda.config import PROJECT_ROOT, get_settings
from eda.audit.models import AuditRecord
from eda.conversation.checkpoint import thread_lock

AUDIT_WARNING = "运行记录未保存；分析结果不受影响。"


def audit_path(path, business_path, checkpoint_path):
    target = Path(path).resolve()
    settings = get_settings()
    protected = (business_path, checkpoint_path, settings.business_db_path,
                 settings.fixture_db_path, settings.checkpoint_db_path,
                 PROJECT_ROOT / "data/business.db", PROJECT_ROOT / "data/fixture.db")
    for other in protected:
        other = Path(other).resolve()
        if target == other or (target.exists() and other.exists() and target.samefile(other)):
            raise ValueError("audit_path_rejected")
    if target.exists():
        if target.stat().st_nlink > 1:
            raise ValueError("audit_path_rejected")
        if target.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("audit_capacity")
        for line in target.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            if not isinstance(raw.get("node_path"), list):
                raise ValueError("audit_schema")
            raw["node_path"] = tuple(raw["node_path"])
            AuditRecord.model_validate(raw)
    return target


def write_record(path, record, *, business_path, checkpoint_path):
    try:
        record = AuditRecord.model_validate(record)
        target = audit_path(path, business_path, checkpoint_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with thread_lock(target, "audit"):
            target = audit_path(path, business_path, checkpoint_path)
            existing = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()] if target.exists() else []
            if any(r["request_id"] == record.request_id or r["run_id"] == record.run_id for r in existing):
                return None
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(record.model_dump_json() + "\n")
        return None
    except Exception:
        return AUDIT_WARNING
