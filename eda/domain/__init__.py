"""Domain vocabulary and row models for the fictional e-commerce dataset."""

from eda.domain.enums import (
    CATEGORIES,
    CHANNELS,
    CUSTOMER_TIERS,
    EXCLUDED_FROM_REVENUE_STATUSES,
    REGIONS,
    REVENUE_STATUSES,
    Category,
    Channel,
    CustomerTier,
    OrderStatus,
    Region,
)
from eda.domain.models import Customer, Dataset, Order, OrderItem, Product

__all__ = [
    "CATEGORIES",
    "CHANNELS",
    "CUSTOMER_TIERS",
    "EXCLUDED_FROM_REVENUE_STATUSES",
    "REGIONS",
    "REVENUE_STATUSES",
    "Category",
    "Channel",
    "CustomerTier",
    "OrderStatus",
    "Region",
    "Customer",
    "Dataset",
    "Order",
    "OrderItem",
    "Product",
]
