"""Shared test fixtures.

No test in this suite touches the network or an LLM. Every database is built
into pytest's tmp directory, so running the suite never overwrites the
developer's own data/business.db.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from eda.data.build_db import build_database
from eda.data.fixture_dataset import load_fixture_dataset
from eda.data.generator import DemoDataSpec, generate_demo_dataset
from eda.data.schema import read_schema_sql
from eda.db import connect_for_build, connect_readonly
from eda.domain.models import Dataset

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_METRICS_PATH = Path(__file__).parent / "data" / "expected_fixture_metrics.json"

#: A smaller demo spec so the suite stays fast; the shape is what matters.
TEST_DEMO_SPEC = DemoDataSpec(n_orders=800, n_customers=120)


@pytest.fixture(scope="session")
def expected_fixture_metrics() -> dict[str, Any]:
    """Hand-computed answers for the fixture dataset (a test asset, not code)."""
    return json.loads(EXPECTED_METRICS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_dataset() -> Dataset:
    return load_fixture_dataset()


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory, fixture_dataset: Dataset) -> Path:
    db_path = tmp_path_factory.mktemp("fixture-db") / "fixture.db"
    build_database(fixture_dataset, db_path)
    return db_path


@pytest.fixture(scope="session")
def fixture_conn(fixture_db: Path):
    conn = connect_readonly(fixture_db)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def demo_dataset() -> Dataset:
    return generate_demo_dataset(TEST_DEMO_SPEC)


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory, demo_dataset: Dataset) -> Path:
    db_path = tmp_path_factory.mktemp("demo-db") / "business.db"
    build_database(demo_dataset, db_path)
    return db_path


@pytest.fixture(scope="session")
def demo_conn(demo_db: Path):
    conn = connect_readonly(demo_db)
    yield conn
    conn.close()


@pytest.fixture
def empty_writable_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Schema only, no rows, writable -- for constraint probing."""
    conn = connect_for_build(tmp_path / "probe.db")
    conn.executescript(read_schema_sql())
    yield conn
    conn.close()
