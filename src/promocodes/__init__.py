"""Promotion engine for discounts, validity windows and atomic redemption."""

from .discounts import Discount, FixedDiscount, PercentageDiscount
from .money import Money, minor_unit_exponent

__all__ = [
    "Discount",
    "FixedDiscount",
    "Money",
    "PercentageDiscount",
    "__version__",
    "minor_unit_exponent",
]

__version__ = "0.1.0"
