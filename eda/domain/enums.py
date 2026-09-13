"""Controlled vocabularies for the fictional e-commerce dataset.

Every value that appears in a database CHECK constraint is defined here exactly
once, so the DDL, the data generators and the metric layer cannot drift apart.
"""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    """Lifecycle status of an order.

    Revenue semantics (stage 1, simplified -- see docs/metrics.md):
      PAID / COMPLETED -> counted as revenue
      PENDING          -> not yet paid, excluded
      CANCELLED        -> never paid, excluded
      REFUNDED         -> excluded in full; the v1 metric does *not* net refunds
                          out of PAID/COMPLETED orders, it simply drops orders
                          whose current status is REFUNDED.
    """

    PENDING = "pending"
    PAID = "paid"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Region(StrEnum):
    """Coarse sales region. Deliberately coarse: no addresses, no geo points."""

    EAST = "华东"
    NORTH = "华北"
    SOUTH = "华南"
    CENTRAL = "华中"
    SOUTHWEST = "西南"


class Category(StrEnum):
    """Product category."""

    PHONE_DIGITAL = "手机数码"
    HOME_APPLIANCE = "家用电器"
    APPAREL = "服饰鞋包"
    FOOD_FRESH = "食品生鲜"
    BEAUTY_CARE = "美妆个护"
    HOME_LIVING = "家居日用"


class Channel(StrEnum):
    """Acquisition / ordering channel."""

    WEB = "web"
    APP = "app"
    MINI_PROGRAM = "mini_program"


class CustomerTier(StrEnum):
    """Loyalty tier. Not personal data, just a bucket label."""

    NEW = "new"
    REGULAR = "regular"
    VIP = "vip"


#: Statuses whose order lines are summed into the v1 revenue metric.
REVENUE_STATUSES: tuple[str, ...] = (OrderStatus.PAID, OrderStatus.COMPLETED)

#: Statuses explicitly excluded from the v1 revenue metric.
EXCLUDED_FROM_REVENUE_STATUSES: tuple[str, ...] = (
    OrderStatus.PENDING,
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
)

REGIONS: tuple[str, ...] = tuple(r.value for r in Region)
CATEGORIES: tuple[str, ...] = tuple(c.value for c in Category)
CHANNELS: tuple[str, ...] = tuple(c.value for c in Channel)
CUSTOMER_TIERS: tuple[str, ...] = tuple(t.value for t in CustomerTier)
ORDER_STATUSES: tuple[str, ...] = tuple(s.value for s in OrderStatus)

assert set(REVENUE_STATUSES) | set(EXCLUDED_FROM_REVENUE_STATUSES) == set(
    ORDER_STATUSES
), "every order status must be classified as revenue or non-revenue"
