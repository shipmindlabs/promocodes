"""Promotion engine for discounts, validity windows and atomic redemption."""

from .codes import PromoCode, PromoCodeRejected, Rejection
from .discounts import Discount, FixedDiscount, PercentageDiscount
from .money import Money, minor_unit_exponent
from .redemption import (
    InMemoryRedemptionStore,
    Redemption,
    RedemptionConflict,
    RedemptionStore,
    Reservation,
    redeem,
)
from .validity import (
    Audience,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    normalize_customer,
)

__all__ = [
    "Audience",
    "Discount",
    "FixedDiscount",
    "InMemoryRedemptionStore",
    "Money",
    "PercentageDiscount",
    "PromoCode",
    "PromoCodeRejected",
    "Redemption",
    "RedemptionConflict",
    "RedemptionStore",
    "Rejection",
    "Reservation",
    "UsageCounts",
    "UsageLimits",
    "ValidityWindow",
    "__version__",
    "minor_unit_exponent",
    "normalize_customer",
    "redeem",
]

__version__ = "0.1.0"
