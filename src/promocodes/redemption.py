"""Redemption that stays correct when two callbacks race for the same code.

The cap on a promo code is only as strong as the write that spends it. The
naive shape::

    counts = load(code)
    counts.total += 1
    save(counts)

is a read-modify-write with a gap in the middle. Two payment webhooks that
land at the same time both read ``total = 0``, both write ``1``, and a code
limited to a single redemption has been spent twice.

The guarantee offered here is: **a redemption is recorded only if the counters
it was checked against are still the ones in storage.** The check and the
increment are welded together by :meth:`RedemptionStore.record`, which is a
compare-and-set. When the counters moved under us the store refuses the write,
:func:`redeem` re-reads, re-runs the rules and tries again -- so the loser of a
race either redeems against fresh counts or is rejected with the usual
:class:`~promocodes.Rejection`. No caller can observe a count above the cap.

Idempotency sits in front of all of it. A redelivered webhook carries the key
of a redemption that already happened, so :meth:`RedemptionStore.replay_for`
is consulted before any rule runs: a code capped at one redemption per
customer would otherwise refuse its own replay with ``customer_limit_reached``,
and a code whose window has closed since the payment would refuse it with
``expired``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from .codes import PromoCode
from .money import Money
from .validity import UsageCounts, normalize_customer

__all__ = [
    "DEFAULT_ATTEMPTS",
    "InMemoryRedemptionStore",
    "Redemption",
    "RedemptionConflict",
    "RedemptionStore",
    "Reservation",
    "redeem",
]

DEFAULT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Reservation:
    """The outcome of a successful write: the counts including that redemption."""

    usage: UsageCounts
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class Redemption:
    """A code that was actually spent, and what it was worth."""

    code: str
    customer: Optional[str]
    discount: Money
    total: Money
    usage: UsageCounts
    redeemed_at: datetime
    idempotency_key: Optional[str] = None
    replayed: bool = False


class RedemptionConflict(RuntimeError):
    """Raised when the counters kept moving and no attempt could commit."""

    def __init__(self, code: str, attempts: int) -> None:
        super().__init__(
            f"{code}: could not record a redemption in {attempts} attempts "
            "because concurrent redemptions kept changing the counts"
        )
        self.code = code
        self.attempts = attempts


@runtime_checkable
class RedemptionStore(Protocol):
    """Where redemption counters live, with the atomicity this engine needs.

    An implementation is free to use a row per code, a Redis hash or anything
    else, as long as :meth:`record` holds up under concurrent callers from
    different processes.
    """

    def usage_for(self, code: str, *, customer: Optional[str]) -> UsageCounts:
        """Return the redemptions recorded so far for the code and customer.

        This read may be stale: it only produces the value the rules are
        checked against, and :meth:`record` decides whether that value still
        holds. ``for_customer`` must be ``0`` when ``customer`` is ``None``.
        """

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Optional[Reservation]:
        """Return the redemption already recorded for ``idempotency_key``.

        :func:`redeem` asks this before the rules run, so a redelivered
        webhook is answered from its key instead of meeting the cap it spent
        itself or a window that has closed since. Return ``None`` when the key
        is unknown; a hit carries the counts stored for that key with
        ``replayed=True`` and moves nothing.
        """

    def record(
        self,
        code: str,
        *,
        customer: Optional[str],
        expected: UsageCounts,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Reservation]:
        """Increment the counters, but only if they still equal ``expected``.

        Returns the counts including the new redemption, or ``None`` when the
        stored counts have moved on -- the caller then re-checks the rules
        against the fresh counts instead of overspending the cap.

        The comparison and the increment must happen as one atomic step, for
        example a single conditional statement::

            UPDATE promo_usage
               SET total = total + 1, for_customer = for_customer + 1
             WHERE code = :code AND customer = :customer
               AND total = :expected_total
               AND for_customer = :expected_for_customer

        with an affected-row count of zero meaning ``None``. Reading first and
        writing after -- in two statements, or in two transactions -- does not
        satisfy this contract.

        When ``idempotency_key`` is given, a key that was already recorded for
        this code must not increment anything a second time: return the counts
        stored for that key with ``replayed=True``, as :meth:`replay_for`
        would. Back the key with a unique constraint written in the same
        transaction as the increment, so two deliveries that both miss the
        lookup cannot spend the code twice.
        """


class InMemoryRedemptionStore:
    """Reference store for tests and single-process use.

    It honours the protocol with a lock rather than a database, so it is
    correct across threads but not across processes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, int] = {}
        self._per_customer: dict[tuple[str, str], int] = {}
        self._keys: dict[tuple[str, str], UsageCounts] = {}

    def usage_for(self, code: str, *, customer: Optional[str] = None) -> UsageCounts:
        key, who = _normalize(code, customer)
        with self._lock:
            return self._counts(key, who)

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Optional[Reservation]:
        key, _ = _normalize(code, None)
        with self._lock:
            seen = self._keys.get((key, idempotency_key))
        if seen is None:
            return None
        return Reservation(seen, replayed=True)

    def record(
        self,
        code: str,
        *,
        customer: Optional[str] = None,
        expected: UsageCounts,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Reservation]:
        key, who = _normalize(code, customer)
        if not isinstance(expected, UsageCounts):
            raise TypeError(
                f"expected must be UsageCounts, got {type(expected).__name__}"
            )
        with self._lock:
            if idempotency_key is not None:
                seen = self._keys.get((key, idempotency_key))
                if seen is not None:
                    return Reservation(seen, replayed=True)
            current = self._counts(key, who)
            if current != expected:
                return None
            self._totals[key] = current.total + 1
            for_customer = 0
            if who is not None:
                for_customer = current.for_customer + 1
                self._per_customer[(key, who)] = for_customer
            recorded = UsageCounts(current.total + 1, for_customer)
            if idempotency_key is not None:
                self._keys[(key, idempotency_key)] = recorded
            return Reservation(recorded)

    def _counts(self, code: str, customer: Optional[str]) -> UsageCounts:
        total = self._totals.get(code, 0)
        if customer is None:
            return UsageCounts(total)
        return UsageCounts(total, self._per_customer.get((code, customer), 0))


def redeem(
    code: PromoCode,
    store: RedemptionStore,
    total: Money,
    *,
    customer: Optional[str] = None,
    now: Optional[datetime] = None,
    idempotency_key: Optional[str] = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> Redemption:
    """Spend ``code`` against ``total``, or raise the reason why it cannot be.

    A known ``idempotency_key`` is answered with the redemption it already
    recorded, before the rules are consulted. Raises
    :class:`~promocodes.PromoCodeRejected` when a rule refuses the code and
    :class:`RedemptionConflict` when contention outlasted ``attempts``.
    """
    if not isinstance(code, PromoCode):
        raise TypeError(f"code must be a PromoCode, got {type(code).__name__}")
    if not isinstance(total, Money):
        raise TypeError(f"total must be Money, got {type(total).__name__}")
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise TypeError(f"attempts must be an int, got {type(attempts).__name__}")
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    if customer is not None:
        customer = normalize_customer(customer)
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str):
            raise TypeError(
                "idempotency_key must be a str or None, got "
                f"{type(idempotency_key).__name__}"
            )
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise ValueError("idempotency_key cannot be empty")

    moment = datetime.now(timezone.utc) if now is None else now

    if idempotency_key is not None:
        replayed = store.replay_for(code.code, idempotency_key=idempotency_key)
        if replayed is not None:
            if not isinstance(replayed, Reservation):
                raise TypeError(
                    "replay_for must return a Reservation or None, got "
                    f"{type(replayed).__name__}"
                )
            discount = code.discount.compute(total)
            return Redemption(
                code=code.code,
                customer=customer,
                discount=discount,
                total=total - discount,
                usage=replayed.usage,
                redeemed_at=moment,
                idempotency_key=idempotency_key,
                replayed=True,
            )

    for _ in range(attempts):
        usage = store.usage_for(code.code, customer=customer)
        if not isinstance(usage, UsageCounts):
            raise TypeError(
                "usage_for must return UsageCounts, got "
                f"{type(usage).__name__}"
            )
        code.validate(customer=customer, usage=usage, total=total, now=moment)
        reservation = store.record(
            code.code,
            customer=customer,
            expected=usage,
            idempotency_key=idempotency_key,
        )
        if reservation is None:
            continue
        discount = code.discount.compute(total)
        return Redemption(
            code=code.code,
            customer=customer,
            discount=discount,
            total=total - discount,
            usage=reservation.usage,
            redeemed_at=moment,
            idempotency_key=idempotency_key,
            replayed=reservation.replayed,
        )

    raise RedemptionConflict(code.code, attempts)


def _normalize(code: str, customer: Optional[str]) -> tuple[str, Optional[str]]:
    if not isinstance(code, str):
        raise TypeError(f"code must be a str, got {type(code).__name__}")
    normalized = code.strip().upper()
    if not normalized:
        raise ValueError("code cannot be empty")
    if customer is None:
        return normalized, None
    return normalized, normalize_customer(customer)
