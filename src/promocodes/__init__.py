"""Promotion engine for discounts, validity windows and atomic redemption."""

from .capture import CaptureRejected, Quote, capture, quote
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
    "CaptureRejected",
    "Discount",
    "FixedDiscount",
    "InMemoryRedemptionStore",
    "Money",
    "PercentageDiscount",
    "PromoCode",
    "PromoCodeRejected",
    "Quote",
    "Redemption",
    "RedemptionConflict",
    "RedemptionStore",
    "Rejection",
    "Reservation",
    "UsageCounts",
    "UsageLimits",
    "ValidityWindow",
    "__version__",
    "capture",
    "minor_unit_exponent",
    "normalize_customer",
    "quote",
    "redeem",
]

__version__ = "0.1.0"
