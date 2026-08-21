"""Rules that gate a promo code: time window, usage caps and audience."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

__all__ = [
    "Audience",
    "UsageCounts",
    "UsageLimits",
    "ValidityWindow",
    "normalize_customer",
]


def normalize_customer(customer: str) -> str:
    """Return the canonical form of a customer identifier."""
    if not isinstance(customer, str):
        raise TypeError(f"customer must be a str, got {type(customer).__name__}")
    normalized = customer.strip()
    if not normalized:
        raise ValueError("customer identifier cannot be empty")
    return normalized


def _require_aware(moment: datetime, name: str) -> datetime:
    if not isinstance(moment, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(moment).__name__}")
    if moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def _require_cap(value: object, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int or None, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def _require_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} cannot be negative, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class ValidityWindow:
    """The half-open interval ``[starts_at, ends_at)`` a code is live in."""

    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.starts_at is not None:
            _require_aware(self.starts_at, "starts_at")
        if self.ends_at is not None:
            _require_aware(self.ends_at, "ends_at")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at")

    @classmethod
    def open(cls) -> ValidityWindow:
        """A window that never opens nor closes."""
        return cls()

    @property
    def is_open_ended(self) -> bool:
        return self.starts_at is None and self.ends_at is None

    def has_started(self, moment: datetime) -> bool:
        _require_aware(moment, "moment")
        return self.starts_at is None or moment >= self.starts_at

    def has_ended(self, moment: datetime) -> bool:
        _require_aware(moment, "moment")
        return self.ends_at is not None and moment >= self.ends_at

    def contains(self, moment: datetime) -> bool:
        return self.has_started(moment) and not self.has_ended(moment)


@dataclass(frozen=True, slots=True)
class UsageLimits:
    """How often a code may be redeemed in total and by a single customer."""

    total: Optional[int] = None
    per_customer: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "total", _require_cap(self.total, "total"))
        object.__setattr__(
            self, "per_customer", _require_cap(self.per_customer, "per_customer")
        )

    @classmethod
    def unlimited(cls) -> UsageLimits:
        return cls()

    @property
    def requires_customer(self) -> bool:
        """A per-customer cap can only be enforced against a known customer."""
        return self.per_customer is not None

    def total_reached(self, redemptions: int) -> bool:
        _require_count(redemptions, "redemptions")
        return self.total is not None and redemptions >= self.total

    def customer_reached(self, redemptions: int) -> bool:
        _require_count(redemptions, "redemptions")
        return self.per_customer is not None and redemptions >= self.per_customer


@dataclass(frozen=True, slots=True)
class UsageCounts:
    """Redemptions recorded so far, globally and for one customer."""

    total: int = 0
    for_customer: int = 0

    def __post_init__(self) -> None:
        _require_count(self.total, "total")
        _require_count(self.for_customer, "for_customer")
        if self.for_customer > self.total:
            raise ValueError(
                "for_customer cannot exceed total: "
                f"{self.for_customer} > {self.total}"
            )


@dataclass(frozen=True, slots=True)
class Audience:
    """Who may use a code: everyone, or a fixed set of customers."""

    customers: Optional[frozenset[str]] = None

    def __post_init__(self) -> None:
        if self.customers is None:
            return
        if isinstance(self.customers, str):
            raise TypeError("customers must be an iterable of identifiers, not a str")
        assigned = frozenset(normalize_customer(one) for one in self.customers)
        if not assigned:
            raise ValueError("an assigned code needs at least one customer")
        object.__setattr__(self, "customers", assigned)

    @classmethod
    def public(cls) -> Audience:
        return cls()

    @classmethod
    def assigned_to(cls, *customers: str) -> Audience:
        return cls(frozenset(customers))

    @classmethod
    def of(cls, customers: Iterable[str]) -> Audience:
        return cls(frozenset(customers))

    @property
    def is_public(self) -> bool:
        return self.customers is None

    def admits(self, customer: Optional[str]) -> bool:
        if self.customers is None:
            return True
        if customer is None:
            return False
        return normalize_customer(customer) in self.customers
