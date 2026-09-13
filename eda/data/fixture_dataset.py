"""The small hand-written fixture dataset.

Rows live in ``eda/data/fixtures/*.csv`` -- 4 customers, 5 products, 8 orders
and 12 order lines, small enough that every metric can be recomputed by hand on
paper. The hand calculation is written out in ``docs/metrics.md`` and the
resulting numbers are stored as a test asset in
``tests/data/expected_fixture_metrics.json``.

Deliberately, the expected answers are NOT in this module and not anywhere else
under ``eda/``: runtime code must never contain baked-in answers.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from eda.domain.models import Customer, Dataset, Order, OrderItem, Product

FIXTURE_NAME = "fixture"
FIXTURES_DIR = Path(__file__).with_name("fixtures")

_INT_FIELDS = {"list_price_cents", "is_active", "quantity", "unit_price_cents"}


def _read_csv(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / f"{name}.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"fixture file is empty: {path}")
    return [
        {key: (int(value) if key in _INT_FIELDS else value) for key, value in row.items()}
        for row in rows
    ]


def load_fixture_dataset() -> Dataset:
    """Load, validate and return the fixture dataset.

    ``Dataset`` validation checks referential integrity, so a typo in a CSV
    fails here rather than halfway through an INSERT.
    """
    return Dataset(
        name=FIXTURE_NAME,
        customers=tuple(Customer(**row) for row in _read_csv("customers")),
        products=tuple(Product(**row) for row in _read_csv("products")),
        orders=tuple(Order(**row) for row in _read_csv("orders")),
        order_items=tuple(OrderItem(**row) for row in _read_csv("order_items")),
    )
