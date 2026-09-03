"""The second check: the rules re-run when the payment confirms.

A cart is priced when the buyer builds it and charged when the payment
provider confirms. Between those two moments the validity window can close,
the last redemption can be taken by somebody else, or the basket can shrink
below the minimum the code demands. Checking once, at checkout, hands out
discounts the rules no longer allow.

:func:`quote` prices the cart; :func:`capture` runs every rule again against
the clock and the counters of the moment the money moves, and refuses with
:class:`CaptureRejected` -- a :class:`~promocodes.PromoCodeRejected` carrying
the quote that went stale, so a checkout API can tell the buyer the code
expired while they were paying.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .codes import PromoCode, PromoCodeRejected, Rejection
from .money import Money
from .redemption import DEFAULT_ATTEMPTS, Redemption, RedemptionStore, redeem
from .validity import UsageCounts, normalize_customer

__all__ = ["CaptureRejected", "Quote", "capture", "quote"]

_NO_USAGE = UsageCounts()


@dataclass(frozen=True, slots=True)
class Quote:
    """What a code was worth when the cart was priced.

    A quote is a plain value: it can be stored next to the payment intent and
    handed back to :func:`capture` when the provider confirms.
    """

    code: str
    customer: Optional[str]
    total: Money
    discount: Money
    payable: Money
    quoted_at: datetime
    expires_at: Optional[datetime] = None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Return whether the code's window has closed since it was quoted."""
        moment = datetime.now(timezone.utc) if now is None else now
        return self.expires_at is not None and moment >= self.expires_at


def quote(
    promo: PromoCode,
    total: Money,
    *,
    customer: Optional[str] = None,
    usage: UsageCounts = _NO_USAGE,
    now: Optional[datetime] = None,
) -> Quote:
    """Price ``total`` with ``promo``, or raise the reason it cannot be used.

    Raises :class:`~promocodes.PromoCodeRejected`; nothing is spent here, the
    counters only move in :func:`capture`.
    """
    if not isinstance(promo, PromoCode):
        raise TypeError(f"promo must be a PromoCode, got {type(promo).__name__}")
    if not isinstance(total, Money):
        raise TypeError(f"total must be Money, got {type(total).__name__}")
    if customer is not None:
        customer = normalize_customer(customer)
    moment = datetime.now(timezone.utc) if now is None else now

    promo.validate(customer=customer, usage=usage, total=total, now=moment)
    discount = promo.discount.compute(total)
    return Quote(
        code=promo.code,
        customer=customer,
        total=total,
        discount=discount,
        payable=total - discount,
        quoted_at=moment,
        expires_at=promo.window.ends_at,
    )


class CaptureRejected(PromoCodeRejected):
    """Raised when a code that passed at checkout is refused at capture."""

    def __init__(self, code: str, rejection: Rejection, quote: Quote) -> None:
        super().__init__(code, rejection)
        self.quote = quote


def capture(
    promo: PromoCode,
    store: RedemptionStore,
    quoted: Quote,
    *,
    total: Optional[Money] = None,
    now: Optional[datetime] = None,
    idempotency_key: Optional[str] = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> Redemption:
    """Spend the code behind ``quoted``, re-running every rule at payment time.

    Pass ``total`` when the basket changed since checkout: the discount is
    recomputed against the amount actually charged. Raises
    :class:`CaptureRejected` when a rule refuses the code now and
    :class:`~promocodes.RedemptionConflict` when contention outlasted
    ``attempts``. Nothing is recorded unless the code passes the second check.
    """
    if not isinstance(promo, PromoCode):
        raise TypeError(f"promo must be a PromoCode, got {type(promo).__name__}")
    if not isinstance(quoted, Quote):
        raise TypeError(f"quoted must be a Quote, got {type(quoted).__name__}")
    if quoted.code != promo.code:
        raise ValueError(f"quote is for {quoted.code}, not for {promo.code}")
    if total is not None and not isinstance(total, Money):
        raise TypeError(f"total must be Money or None, got {type(total).__name__}")

    charged = quoted.total if total is None else total
    moment = datetime.now(timezone.utc) if now is None else now

    try:
        return redeem(
            promo,
            store,
            charged,
            customer=quoted.customer,
            now=moment,
            idempotency_key=idempotency_key,
            attempts=attempts,
        )
    except PromoCodeRejected as rejected:
        raise CaptureRejected(promo.code, rejected.rejection, quoted) from None
