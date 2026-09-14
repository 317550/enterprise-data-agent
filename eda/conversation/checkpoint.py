"""Local checkpoint storage, isolated from every business database connection."""

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import re

from langgraph.checkpoint.sqlite import SqliteSaver

from eda.config import get_settings
from eda.db import connect_readonly, table_names, view_names


class ConversationError(ValueError):
    """Code-only errors; never surface raw storage or request material."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_thread(thread_id: str) -> None:
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", thread_id):
        raise ConversationError("invalid_thread_id")


def checkpoint_path(path: str | Path, business_path: str | Path) -> Path:
    target = Path(path).resolve()
    settings = get_settings()
    for protected in (Path(business_path), settings.business_db_path, settings.fixture_db_path):
        protected = protected.resolve()
        if target == protected or (target.exists() and protected.exists() and target.samefile(protected)):
            raise ConversationError("checkpoint_is_business_database")
    # Inspect existing files read-only BEFORE giving the saver a writable handle.
    # Refuse any foreign schema, including a business DB under an unrelated name.
    if target.exists():
        if target.stat().st_nlink > 1:
            raise ConversationError("checkpoint_hardlink_not_supported")
        try:
            conn = connect_readonly(target)
            try:
                if set(table_names(conn)) - {"checkpoints", "writes"} or view_names(conn):
                    raise ConversationError("foreign_checkpoint_database")
            finally:
                conn.close()
        except ConversationError:
            raise
        except Exception:
            raise ConversationError("invalid_checkpoint_database") from None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def thread_lock(path: Path, thread_id: str):
    """Nonblocking OS lock, covering load -> invoke -> synchronous commit.

    Works across service instances and local processes. OS releases the lock
    after a crash. Never unlink lock files: doing so would split lock identity.
    """
    validate_thread(thread_id)
    directory = path.with_name(path.name + ".locks")
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / (sha256(thread_id.encode("ascii")).hexdigest() + ".lock")
    with lock_path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConversationError("thread_busy") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def open_checkpointer(path: Path):
    # LangGraph owns checkpoint SQL. It never uses the business query executor,
    # and the business executor never receives this connection.
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield saver
