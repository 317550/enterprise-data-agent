"""Reproducible demo data generator.

Two properties matter more than realism here:

*Determinism*  -- the same :class:`DemoDataSpec` always produces byte-identical
rows. Only a seeded :class:`random.Random` is used, all prices are computed with
integer arithmetic (no float rounding), and every iteration order is explicit.

*Date independence* -- the date window comes from the spec, never from
``date.today()``. Rebuilding the database next year produces the same data.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda.domain.enums import (
    CATEGORIES,
    Category,
    Channel,
    CustomerTier,
    OrderStatus,
    Region,
)
from eda.domain.models import Customer, Dataset, Order, OrderItem, Product

DEMO_NAME = "demo"

DEFAULT_SEED: Final[int] = 20240101
DEFAULT_START_DATE: Final[str] = "2024-01-01"
DEFAULT_END_DATE: Final[str] = "2024-12-31"


class DemoDataSpec(BaseModel):
    """Everything that influences the generated dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = DEFAULT_SEED
    start_date: str = DEFAULT_START_DATE
    end_date: str = DEFAULT_END_DATE
    n_customers: int = Field(default=400, ge=1, le=100_000)
    n_products_per_category: int = Field(default=5, ge=1, le=200)
    n_orders: int = Field(default=3000, ge=1, le=500_000)

    @model_validator(mode="after")
    def _check_window(self) -> DemoDataSpec:
        if dt.date.fromisoformat(self.start_date) > dt.date.fromisoformat(self.end_date):
            raise ValueError("start_date must not be after end_date")
        return self

    @property
    def start(self) -> dt.date:
        return dt.date.fromisoformat(self.start_date)

    @property
    def end(self) -> dt.date:
        return dt.date.fromisoformat(self.end_date)


# --- Shape of the fictional business -----------------------------------------
# Month index (1..12) -> demand multiplier in percent. Deliberately lumpy so
# that "which month was best?" has a real answer.
_MONTH_WEIGHT_PCT: Final[dict[int, int]] = {
    1: 110, 2: 80, 3: 95, 4: 90, 5: 105, 6: 140,
    7: 95, 8: 100, 9: 105, 10: 120, 11: 180, 12: 130,
}

# Weekday index (Mon=0) -> demand multiplier in percent.
_WEEKDAY_WEIGHT_PCT: Final[tuple[int, ...]] = (95, 90, 95, 100, 115, 130, 125)

_REGION_WEIGHTS: Final[dict[str, int]] = {
    Region.EAST.value: 32,
    Region.NORTH.value: 22,
    Region.SOUTH.value: 20,
    Region.CENTRAL.value: 14,
    Region.SOUTHWEST.value: 12,
}

_CHANNEL_WEIGHTS: Final[dict[str, int]] = {
    Channel.APP.value: 55,
    Channel.WEB.value: 30,
    Channel.MINI_PROGRAM.value: 15,
}

_TIER_WEIGHTS: Final[dict[str, int]] = {
    CustomerTier.NEW.value: 45,
    CustomerTier.REGULAR.value: 42,
    CustomerTier.VIP.value: 13,
}

#: Status mix. 'refunded' exists so that the simplified v1 revenue definition
#: has something concrete to exclude (see docs/metrics.md).
_STATUS_WEIGHTS: Final[dict[str, int]] = {
    OrderStatus.COMPLETED.value: 52,
    OrderStatus.PAID.value: 32,
    OrderStatus.PENDING.value: 7,
    OrderStatus.CANCELLED.value: 6,
    OrderStatus.REFUNDED.value: 3,
}

#: Orders placed by VIP customers are more likely to end up paid.
_VIP_STATUS_WEIGHTS: Final[dict[str, int]] = {
    OrderStatus.COMPLETED.value: 62,
    OrderStatus.PAID.value: 30,
    OrderStatus.PENDING.value: 4,
    OrderStatus.CANCELLED.value: 3,
    OrderStatus.REFUNDED.value: 1,
}

#: category -> (price floor in cents, price step in cents, name stems)
_CATEGORY_CATALOG: Final[dict[str, tuple[int, int, tuple[str, ...]]]] = {
    Category.PHONE_DIGITAL.value: (
        59900, 47000, ("星河手机", "星河平板", "星河耳机", "星河手表", "星河充电宝"),
    ),
    Category.HOME_APPLIANCE.value: (
        39900, 33000, ("清风净化器", "清风电饭煲", "清风扫地机", "清风微波炉", "清风电风扇"),
    ),
    Category.APPAREL.value: (
        9900, 7000, ("轻步运动鞋", "轻步卫衣", "轻步背包", "轻步外套", "轻步棒球帽"),
    ),
    Category.FOOD_FRESH.value: (
        1900, 1500, ("晨采鲜牛奶", "晨采鸡蛋", "晨采蓝莓", "晨采三文鱼", "晨采坚果礼盒"),
    ),
    Category.BEAUTY_CARE.value: (
        6900, 5500, ("初色面霜", "初色洗发水", "初色口红", "初色精华液", "初色牙膏"),
    ),
    Category.HOME_LIVING.value: (
        3900, 3100, ("宜居收纳箱", "宜居四件套", "宜居台灯", "宜居保温杯", "宜居拖把"),
    ),
}

#: Discount buckets as integer percent of list price, so unit prices stay exact.
_DISCOUNT_PCT: Final[tuple[int, ...]] = (100, 95, 90, 85, 80, 75, 70)
_DISCOUNT_WEIGHTS: Final[tuple[int, ...]] = (30, 20, 18, 12, 9, 7, 4)

_LINES_PER_ORDER: Final[tuple[int, ...]] = (1, 2, 3, 4)
_LINES_WEIGHTS: Final[tuple[int, ...]] = (48, 30, 15, 7)

#: Categories that people buy several of at a time.
_BULK_CATEGORIES: Final[frozenset[str]] = frozenset(
    {Category.FOOD_FRESH.value, Category.HOME_LIVING.value, Category.BEAUTY_CARE.value}
)


def _weighted_pick(rng: random.Random, weights: dict[str, int]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _build_products(spec: DemoDataSpec) -> tuple[Product, ...]:
    """Products are fully determined by the spec -- no randomness at all."""
    products: list[Product] = []
    index = 0
    for category in CATEGORIES:
        floor_cents, step_cents, stems = _CATEGORY_CATALOG[category]
        for slot in range(spec.n_products_per_category):
            stem = stems[slot % len(stems)]
            suffix = "" if slot < len(stems) else f" v{slot // len(stems) + 1}"
            index += 1
            products.append(
                Product(
                    product_id=f"P{index:03d}",
                    product_name=f"{stem}{suffix}",
                    category=category,
                    list_price_cents=floor_cents + step_cents * slot,
                    is_active=1,
                )
            )
    return tuple(products)


def _build_customers(spec: DemoDataSpec, rng: random.Random) -> tuple[Customer, ...]:
    """Signup dates sit in the year before the order window, from the spec."""
    signup_first = spec.start - dt.timedelta(days=365)
    signup_span = (spec.end - signup_first).days
    customers: list[Customer] = []
    for index in range(1, spec.n_customers + 1):
        signup = signup_first + dt.timedelta(days=rng.randint(0, signup_span))
        customers.append(
            Customer(
                customer_id=f"C{index:04d}",
                signup_date=signup.isoformat(),
                signup_region=_weighted_pick(rng, _REGION_WEIGHTS),
                signup_channel=_weighted_pick(rng, _CHANNEL_WEIGHTS),
                customer_tier=_weighted_pick(rng, _TIER_WEIGHTS),
            )
        )
    return tuple(customers)


def _order_dates(spec: DemoDataSpec, rng: random.Random) -> list[dt.date]:
    """Sample order dates from the fixed window with monthly/weekday seasonality."""
    days = [
        spec.start + dt.timedelta(days=offset)
        for offset in range((spec.end - spec.start).days + 1)
    ]
    weights = [
        _MONTH_WEIGHT_PCT[day.month] * _WEEKDAY_WEIGHT_PCT[day.weekday()] for day in days
    ]
    sampled = rng.choices(days, weights=weights, k=spec.n_orders)
    return sorted(sampled)


def _pick_quantity(rng: random.Random, category: str) -> int:
    if category in _BULK_CATEGORIES:
        return rng.choices((1, 2, 3, 4, 6), weights=(35, 27, 18, 12, 8), k=1)[0]
    return rng.choices((1, 2, 3), weights=(78, 17, 5), k=1)[0]


def generate_demo_dataset(spec: DemoDataSpec | None = None) -> Dataset:
    """Build the seeded demo dataset.

    Calling this twice with the same spec returns identical data.
    """
    spec = spec or DemoDataSpec()
    rng = random.Random(spec.seed)

    products = _build_products(spec)
    customers = _build_customers(spec, rng)
    products_by_id = {p.product_id: p for p in products}
    other_regions = {
        region: tuple(r for r in _REGION_WEIGHTS if r != region) for region in _REGION_WEIGHTS
    }

    orders: list[Order] = []
    order_items: list[OrderItem] = []

    for sequence, order_date in enumerate(_order_dates(spec, rng), start=1):
        order_id = f"O{sequence:08d}"
        customer = customers[rng.randrange(len(customers))]

        # 9% of orders are placed from a region other than the signup region;
        # orders.order_region is what regional reports must use.
        if rng.randrange(100) < 9:
            candidates = other_regions[customer.signup_region]
            order_region = candidates[rng.randrange(len(candidates))]
        else:
            order_region = customer.signup_region

        status_weights = (
            _VIP_STATUS_WEIGHTS
            if customer.customer_tier == CustomerTier.VIP
            else _STATUS_WEIGHTS
        )
        orders.append(
            Order(
                order_id=order_id,
                customer_id=customer.customer_id,
                order_date=order_date.isoformat(),
                status=_weighted_pick(rng, status_weights),
                order_region=order_region,
                order_channel=_weighted_pick(rng, _CHANNEL_WEIGHTS),
            )
        )

        n_lines = rng.choices(_LINES_PER_ORDER, weights=_LINES_WEIGHTS, k=1)[0]
        # sample() guarantees distinct products, which satisfies the
        # UNIQUE(order_id, product_id) constraint on order_items.
        chosen = rng.sample(sorted(products_by_id), k=n_lines)
        for line_no, product_id in enumerate(chosen, start=1):
            product = products_by_id[product_id]
            discount_pct = rng.choices(_DISCOUNT_PCT, weights=_DISCOUNT_WEIGHTS, k=1)[0]
            # Integer arithmetic: exact and platform independent.
            unit_price_cents = product.list_price_cents * discount_pct // 100
            order_items.append(
                OrderItem(
                    order_item_id=f"{order_id}-{line_no}",
                    order_id=order_id,
                    product_id=product_id,
                    quantity=_pick_quantity(rng, product.category),
                    unit_price_cents=unit_price_cents,
                )
            )

    return Dataset(
        name=DEMO_NAME,
        customers=customers,
        products=products,
        orders=tuple(orders),
        order_items=tuple(order_items),
    )
