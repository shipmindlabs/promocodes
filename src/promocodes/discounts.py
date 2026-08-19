"""Discount kinds: fixed amount and percentage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .money import Money

__all__ = ["Discount", "FixedDiscount", "PercentageDiscount"]


class Discount(ABC):
    """A discount kind turning an order total into the amount taken off it."""

    @abstractmethod
    def _amount_for(self, total: Money) -> Money:
        """Return the discount before it is clamped to the order total."""

    def compute(self, total: Money) -> Money:
        """Return the discount for ``total``, never negative and never larger."""
        if total.is_negative:
            raise ValueError(f"order total cannot be negative: {total}")
        raw = self._amount_for(total)
        if raw.currency != total.currency:
            raise ValueError(
                f"cannot apply a {raw.currency} discount to a {total.currency} total"
            )
        if raw.amount <= 0:
            return Money.zero(total.currency)
        if raw.amount >= total.amount:
            return Money(total.amount, total.currency)
        return raw

    def apply(self, total: Money) -> Money:
        """Return what is left of ``total`` once the discount is taken off."""
        return total - self.compute(total)


@dataclass(frozen=True, slots=True)
class FixedDiscount(Discount):
    """Takes a fixed amount off the order total."""

    amount: Money

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Money):
            raise TypeError(f"amount must be Money, got {type(self.amount).__name__}")
        if self.amount.is_negative:
            raise ValueError(f"a fixed discount cannot be negative: {self.amount}")

    def _amount_for(self, total: Money) -> Money:
        return self.amount


@dataclass(frozen=True, slots=True)
class PercentageDiscount(Discount):
    """Takes a percentage of the order total, rounded to the currency unit."""

    percent: Decimal
    rounding: str = ROUND_HALF_UP

    def __post_init__(self) -> None:
        percent = self.percent
        if isinstance(percent, (int, str)):
            percent = Decimal(percent)
        if not isinstance(percent, Decimal):
            raise TypeError(
                f"percent must be a Decimal, got {type(self.percent).__name__}"
            )
        if not percent.is_finite():
            raise ValueError("percent must be a finite number")
        if not Decimal(0) <= percent <= Decimal(100):
            raise ValueError(f"percent must be between 0 and 100, got {percent}")
        object.__setattr__(self, "percent", percent)

    def _amount_for(self, total: Money) -> Money:
        # Shifting by two digits keeps the product exact, so the only rounding
        # that happens is the explicit one below.
        raw = (total.amount * self.percent).scaleb(-2)
        return Money(raw, total.currency).quantize(self.rounding)
