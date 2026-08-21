"""Promo codes and the checks deciding whether one may be used."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .discounts import Discount
from .validity import (
    Audience,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    normalize_customer,
)

__all__ = ["PromoCode", "PromoCodeRejected", "Rejection"]

_NO_USAGE = UsageCounts()


class Rejection(Enum):
    """Why a promo code cannot be used."""

    NOT_STARTED = "the code is not valid yet"
    EXPIRED = "the code has expired"
    EXHAUSTED = "the code reached its global usage limit"
    CUSTOMER_LIMIT_REACHED = "the customer reached the per-customer limit"
    NOT_ASSIGNED = "the code is not assigned to this customer"
    CUSTOMER_REQUIRED = "the code can only be used by an identified customer"


class PromoCodeRejected(ValueError):
    """Raised when a code is used although its rules do not allow it."""

    def __init__(self, code: str, rejection: Rejection) -> None:
        super().__init__(f"{code}: {rejection.value}")
        self.code = code
        self.rejection = rejection


@dataclass(frozen=True, slots=True)
class PromoCode:
    """A code, the discount it grants and the rules limiting its use."""

    code: str
    discount: Discount
    window: ValidityWindow = ValidityWindow()
    limits: UsageLimits = UsageLimits()
    audience: Audience = Audience()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError(f"code must be a str, got {type(self.code).__name__}")
        code = self.code.strip().upper()
        if not code:
            raise ValueError("code cannot be empty")
        object.__setattr__(self, "code", code)
        if not isinstance(self.discount, Discount):
            raise TypeError(
                f"discount must be a Discount, got {type(self.discount).__name__}"
            )
        if not isinstance(self.window, ValidityWindow):
            raise TypeError(
                f"window must be a ValidityWindow, got {type(self.window).__name__}"
            )
        if not isinstance(self.limits, UsageLimits):
            raise TypeError(
                f"limits must be UsageLimits, got {type(self.limits).__name__}"
            )
        if not isinstance(self.audience, Audience):
            raise TypeError(
                f"audience must be an Audience, got {type(self.audience).__name__}"
            )

    @property
    def is_public(self) -> bool:
        return self.audience.is_public

    def matches(self, entered: str) -> bool:
        """Return whether ``entered`` is this code, ignoring case and padding."""
        return isinstance(entered, str) and entered.strip().upper() == self.code

    def check(
        self,
        *,
        customer: Optional[str] = None,
        usage: UsageCounts = _NO_USAGE,
        now: Optional[datetime] = None,
    ) -> Optional[Rejection]:
        """Return the reason the code cannot be used, or ``None`` if it can."""
        if not isinstance(usage, UsageCounts):
            raise TypeError(f"usage must be UsageCounts, got {type(usage).__name__}")
        moment = datetime.now(timezone.utc) if now is None else now
        if customer is not None:
            customer = normalize_customer(customer)

        if customer is None and (not self.audience.is_public or self.limits.requires_customer):
            return Rejection.CUSTOMER_REQUIRED
        if not self.audience.admits(customer):
            return Rejection.NOT_ASSIGNED
        if not self.window.has_started(moment):
            return Rejection.NOT_STARTED
        if self.window.has_ended(moment):
            return Rejection.EXPIRED
        if self.limits.total_reached(usage.total):
            return Rejection.EXHAUSTED
        if self.limits.customer_reached(usage.for_customer):
            return Rejection.CUSTOMER_LIMIT_REACHED
        return None

    def is_usable(
        self,
        *,
        customer: Optional[str] = None,
        usage: UsageCounts = _NO_USAGE,
        now: Optional[datetime] = None,
    ) -> bool:
        return self.check(customer=customer, usage=usage, now=now) is None

    def validate(
        self,
        *,
        customer: Optional[str] = None,
        usage: UsageCounts = _NO_USAGE,
        now: Optional[datetime] = None,
    ) -> None:
        """Raise :class:`PromoCodeRejected` unless the code may be used."""
        rejection = self.check(customer=customer, usage=usage, now=now)
        if rejection is not None:
            raise PromoCodeRejected(self.code, rejection)
