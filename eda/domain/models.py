"""Pydantic row models.

These models are the validation gate in front of the database: both the manual
fixture loader and the seeded demo generator must produce objects that pass
them before a single row is inserted. They mirror the DDL in eda/data/schema.sql.

Money is always an integer number of cents (分). There are no floats anywhere in
the data model, so sums are exact.
"""

from __future__ import annotations

import datetime as _dt
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eda.domain.enums import Category, Channel, CustomerTier, OrderStatus, Region

_ID_PATTERN = re.compile(r"^[A-Z]{1,4}[0-9]{3,9}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_iso_date(value: str) -> str:
    """Accept only zero-padded ISO calendar dates, e.g. '2024-03-07'."""
    if not _DATE_PATTERN.match(value):
        raise ValueError(f"date must look like YYYY-MM-DD, got {value!r}")
    # Rejects 2024-02-31 and friends.
    _dt.date.fromisoformat(value)
    return value


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Customer(_Row):
    """Grain: one row per registered customer.

    Privacy: pseudonymous surrogate id only. No name, phone, e-mail, address,
    birthday or ID number is stored or generated anywhere in this project.
    """

    customer_id: str = Field(description="surrogate pseudonymous id, e.g. 'C0001'")
    signup_date: str = Field(description="ISO date the account was created")
    signup_region: str = Field(description="region recorded at signup time")
    signup_channel: Channel
    customer_tier: CustomerTier

    @field_validator("customer_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"customer_id must match {_ID_PATTERN.pattern}, got {v!r}")
        return v

    @field_validator("signup_date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        return _check_iso_date(v)

    @field_validator("signup_region")
    @classmethod
    def _valid_region(cls, v: str) -> str:
        return Region(v).value


class Product(_Row):
    """Grain: one row per sellable product (SKU).

    ``list_price_cents`` is the catalogue price. It is *not* the transaction
    price -- what a customer actually paid lives on ``order_items``.
    """

    product_id: str = Field(description="surrogate id, e.g. 'P001'")
    product_name: str = Field(min_length=1, max_length=80, description="fictional name")
    category: str
    list_price_cents: int = Field(gt=0, description="catalogue price in cents")
    is_active: int = Field(ge=0, le=1)

    @field_validator("product_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"product_id must match {_ID_PATTERN.pattern}, got {v!r}")
        return v

    @field_validator("category")
    @classmethod
    def _valid_category(cls, v: str) -> str:
        return Category(v).value


class Order(_Row):
    """Grain: one row per order (the order header).

    ``order_region`` is the region *at the time the order happened*, which may
    differ from the customer's ``signup_region``; regional reports use this
    column so that history does not change when a customer moves.
    """

    order_id: str = Field(description="surrogate id, e.g. 'O00000001'")
    customer_id: str
    order_date: str = Field(description="ISO date the order was placed")
    status: OrderStatus
    order_region: str
    order_channel: Channel

    @field_validator("order_id", "customer_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"id must match {_ID_PATTERN.pattern}, got {v!r}")
        return v

    @field_validator("order_date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        return _check_iso_date(v)

    @field_validator("order_region")
    @classmethod
    def _valid_region(cls, v: str) -> str:
        return Region(v).value


class OrderItem(_Row):
    """Grain: one row per (order, product) line.

    ``UNIQUE(order_id, product_id)`` in the DDL means a product appears at most
    once per order, so the same product is never split across two lines.
    ``line_amount_cents`` is deliberately *not* stored: it is always derived as
    ``quantity * unit_price_cents`` by the SQL views, so it can never go stale.
    """

    order_item_id: str = Field(description="surrogate id, e.g. 'O00000001-1'")
    order_id: str
    product_id: str
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0, description="price actually paid, in cents")

    @field_validator("order_id", "product_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"id must match {_ID_PATTERN.pattern}, got {v!r}")
        return v

    @field_validator("order_item_id")
    @classmethod
    def _valid_item_id(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{1,4}[0-9]{3,9}-[0-9]{1,3}$", v):
            raise ValueError(f"order_item_id must look like 'O00000001-1', got {v!r}")
        return v

    @property
    def line_amount_cents(self) -> int:
        return self.quantity * self.unit_price_cents


class Dataset(BaseModel):
    """A complete, internally consistent dataset ready to be written to SQLite."""

    model_config = ConfigDict(extra="forbid")

    name: str
    customers: tuple[Customer, ...]
    products: tuple[Product, ...]
    orders: tuple[Order, ...]
    order_items: tuple[OrderItem, ...]

    @model_validator(mode="after")
    def _check_referential_integrity(self) -> Dataset:
        """Fail fast in Python, before SQLite gets a chance to complain."""
        customer_ids = {c.customer_id for c in self.customers}
        product_ids = {p.product_id for p in self.products}
        order_ids = {o.order_id for o in self.orders}

        if len(customer_ids) != len(self.customers):
            raise ValueError("duplicate customer_id")
        if len(product_ids) != len(self.products):
            raise ValueError("duplicate product_id")
        if len(order_ids) != len(self.orders):
            raise ValueError("duplicate order_id")

        item_ids = {i.order_item_id for i in self.order_items}
        if len(item_ids) != len(self.order_items):
            raise ValueError("duplicate order_item_id")

        pairs = {(i.order_id, i.product_id) for i in self.order_items}
        if len(pairs) != len(self.order_items):
            raise ValueError("duplicate (order_id, product_id) in order_items")

        for order in self.orders:
            if order.customer_id not in customer_ids:
                raise ValueError(f"order {order.order_id} references unknown customer")

        for item in self.order_items:
            if item.order_id not in order_ids:
                raise ValueError(f"order_item {item.order_item_id} references unknown order")
            if item.product_id not in product_ids:
                raise ValueError(f"order_item {item.order_item_id} references unknown product")

        orders_without_items = order_ids - {i.order_id for i in self.order_items}
        if orders_without_items:
            raise ValueError(
                "every order must have at least one line; offenders: "
                + ", ".join(sorted(orders_without_items))
            )
        return self

    @property
    def row_counts(self) -> dict[str, int]:
        return {
            "customers": len(self.customers),
            "products": len(self.products),
            "orders": len(self.orders),
            "order_items": len(self.order_items),
        }
