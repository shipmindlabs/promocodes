"""Money value type shared by every discount computation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Union

__all__ = ["Money", "minor_unit_exponent"]

_ZERO_DECIMAL_CURRENCIES = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "ISK",
        "JPY",
        "KMF",
        "KRW",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)
_THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

AmountLike = Union[Decimal, int, str]


def minor_unit_exponent(currency: str) -> int:
    """Return how many fractional digits the currency is settled in."""
    code = currency.upper()
    if code in _ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in _THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in a single ISO 4217 currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        code = self.currency
        if not isinstance(code, str) or len(code) != 3 or not code.isalpha():
            raise ValueError(
                f"currency must be a 3-letter ISO 4217 code, got {self.currency!r}"
            )
        object.__setattr__(self, "currency", code.upper())

        amount = self.amount
        if isinstance(amount, (int, str)):
            amount = Decimal(amount)
        if not isinstance(amount, Decimal):
            raise TypeError(f"amount must be a Decimal, got {type(self.amount).__name__}")
        if not amount.is_finite():
            raise ValueError("amount must be a finite number")
        object.__setattr__(self, "amount", amount)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal(0), currency)

    def quantize(self, rounding: str = ROUND_HALF_UP) -> Money:
        """Round to the smallest unit the currency can actually be paid in."""
        exponent = Decimal(1).scaleb(-minor_unit_exponent(self.currency))
        return Money(self.amount.quantize(exponent, rounding=rounding), self.currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def _require_same_currency(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise ValueError(
                f"currency mismatch: {self.currency} and {other.currency}"
            )
        return other

    def __add__(self, other: Money) -> Money:
        return Money(self.amount + self._require_same_currency(other).amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount - self._require_same_currency(other).amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._require_same_currency(other).amount

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._require_same_currency(other).amount

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._require_same_currency(other).amount

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._require_same_currency(other).amount

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
