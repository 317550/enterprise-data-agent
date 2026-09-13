"""The build script creates a database once and then protects it."""

from __future__ import annotations

from pathlib import Path

import pytest

from eda.data.build_db import (
    EXIT_OK,
    EXIT_TARGET_EXISTS,
    TargetExistsError,
    build_database,
    main,
)
from eda.data.fixture_dataset import load_fixture_dataset
from eda.db import connect_readonly, fetch_all, fetch_one
from eda.domain.models import Dataset


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_fixture_dataset()


def test_first_build_creates_a_verified_database(tmp_path: Path, dataset: Dataset) -> None:
    db_path = tmp_path / "nested" / "dir" / "business.db"
    counts = build_database(dataset, db_path)

    assert db_path.is_file()
    assert counts == dataset.row_counts

    conn = connect_readonly(db_path)
    try:
        assert fetch_all(conn, "PRAGMA foreign_key_check") == []
        row = fetch_one(conn, "SELECT COUNT(*) AS n FROM orders")
        assert row is not None and row["n"] == len(dataset.orders)
    finally:
        conn.close()


def test_rebuild_is_refused_and_keeps_the_existing_file(
    tmp_path: Path, dataset: Dataset
) -> None:
    db_path = tmp_path / "business.db"
    build_database(dataset, db_path)
    original_bytes = db_path.read_bytes()

    with pytest.raises(TargetExistsError, match="already exists"):
        build_database(dataset, db_path)

    assert db_path.read_bytes() == original_bytes, "refused build must not modify the file"


def test_refusal_also_applies_to_an_unrelated_existing_file(tmp_path: Path, dataset) -> None:
    """Even a non-database file at the target path is left alone."""
    db_path = tmp_path / "business.db"
    db_path.write_text("important notes, definitely not a database", encoding="utf-8")

    with pytest.raises(TargetExistsError):
        build_database(dataset, db_path)

    assert db_path.read_text(encoding="utf-8").startswith("important notes")


def test_force_overwrite_replaces_the_database(tmp_path: Path, dataset: Dataset) -> None:
    db_path = tmp_path / "business.db"
    build_database(dataset, db_path)

    first_order_id = dataset.orders[0].order_id
    smaller = Dataset(
        name="subset",
        customers=dataset.customers,
        products=dataset.products,
        orders=dataset.orders[:1],
        order_items=tuple(i for i in dataset.order_items if i.order_id == first_order_id),
    )
    counts = build_database(smaller, db_path, force_overwrite=True)

    assert counts["orders"] == 1
    conn = connect_readonly(db_path)
    try:
        row = fetch_one(conn, "SELECT COUNT(*) AS n FROM orders")
        assert row is not None and row["n"] == 1
    finally:
        conn.close()


def test_no_staging_file_is_left_behind(tmp_path: Path, dataset: Dataset) -> None:
    db_path = tmp_path / "business.db"
    build_database(dataset, db_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["business.db"]


def test_cli_builds_then_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "fixture.db"
    argv = ["--dataset", "fixture", "--db", str(db_path)]

    assert main(argv) == EXIT_OK
    assert "order_items" in capsys.readouterr().out

    assert main(argv) == EXIT_TARGET_EXISTS
    assert "already exists" in capsys.readouterr().err

    assert main([*argv, "--force-overwrite", "--quiet"]) == EXIT_OK


def test_cli_demo_dataset_is_reproducible_end_to_end(tmp_path: Path) -> None:
    """Two independent CLI builds with the same seed give the same row contents."""
    paths = []
    for name in ("a.db", "b.db"):
        db_path = tmp_path / name
        assert (
            main(
                [
                    "--dataset", "demo",
                    "--db", str(db_path),
                    "--orders", "150",
                    "--customers", "40",
                    "--quiet",
                ]
            )
            == EXIT_OK
        )
        paths.append(db_path)

    dumps = []
    for db_path in paths:
        conn = connect_readonly(db_path)
        try:
            dumps.append(
                [
                    tuple(row)
                    for row in fetch_all(
                        conn,
                        "SELECT order_item_id, order_id, product_id, quantity, "
                        "unit_price_cents FROM order_items ORDER BY order_item_id",
                    )
                ]
            )
        finally:
            conn.close()

    assert dumps[0] == dumps[1]
    assert dumps[0], "expected some order items"
