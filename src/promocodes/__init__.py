"""Promotion engine for discounts, validity windows and atomic redemption."""

from .codes import PromoCode, PromoCodeRejected, Rejection
from .discounts import Discount, FixedDiscount, PercentageDiscount
from .money import Money, minor_unit_exponent
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
    "Money",
    "PercentageDiscount",
    "PromoCode",
    "PromoCodeRejected",
    "Rejection",
    "UsageCounts",
    "UsageLimits",
    "ValidityWindow",
    "__version__",
    "minor_unit_exponent",
    "normalize_customer",
]

__version__ = "0.1.0"
