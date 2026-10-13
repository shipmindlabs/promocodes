from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

import pytest

from promocodes import (
    FixedDiscount,
    InMemoryRedemptionStore,
    Money,
    PercentageDiscount,
    PromoCode,
    PromoCodeRejected,
    Redemption,
    RedemptionConflict,
    RedemptionStore,
    Rejection,
    Reservation,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    redeem,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# A losing thread should re-read the counters rather than give up, so the
# outcome of a race is decided by the rules and not by the retry budget.
PATIENT = 64

TEN = PercentageDiscount(Decimal("10"))


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def run_in_parallel(work: Callable[[int], object], workers: int) -> list[object]:
    """Run ``work(index)`` in ``workers`` threads released at the same instant."""
    barrier = threading.Barrier(workers, timeout=30)
    outcomes: list[object] = [None] * workers

    def task(index: int) -> None:
        try:
            barrier.wait()
            outcomes[index] = work(index)
        except Exception as failure:
            outcomes[index] = failure

    threads = [threading.Thread(target=task, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads)
    return outcomes


def split(outcomes: list[object]) -> tuple[list[Redemption], list[object]]:
    receipts = [one for one in outcomes if isinstance(one, Redemption)]
    failures = [one for one in outcomes if not isinstance(one, Redemption)]
    return receipts, failures


def test_a_single_use_code_is_spent_once_under_a_thundering_herd():
    promo = PromoCode(
        "LASTONE", FixedDiscount(eur("5.00")), limits=UsageLimits(total=1)
    )
    store = InMemoryRedemptionStore()

    outcomes = run_in_parallel(
        lambda _: redeem(promo, store, eur("40.00"), now=START, attempts=PATIENT), 16
    )
    receipts, failures = split(outcomes)

    assert len(receipts) == 1
    assert receipts[0].usage == UsageCounts(1, 0)
    assert receipts[0].total == eur("35.00")
    assert store.usage_for("LASTONE") == UsageCounts(1, 0)
    assert all(isinstance(failure, PromoCodeRejected) for failure in failures)
    assert {failure.rejection for failure in failures} == {Rejection.EXHAUSTED}


def test_a_capped_code_hands_out_exactly_its_cap():
    promo = PromoCode("CAP5", TEN, limits=UsageLimits(total=5))
    store = InMemoryRedemptionStore()

    outcomes = run_in_parallel(
        lambda _: redeem(promo, store, eur("80.00"), now=START, attempts=PATIENT), 24
    )
    receipts, failures = split(outcomes)

    assert len(receipts) == 5
    assert sorted(receipt.usage.total for receipt in receipts) == [1, 2, 3, 4, 5]
    assert all(receipt.discount == eur("8.00") for receipt in receipts)
    assert store.usage_for("CAP5") == UsageCounts(5, 0)
    assert {failure.rejection for failure in failures} == {Rejection.EXHAUSTED}


def test_one_customer_racing_itself_redeems_once():
    promo = PromoCode("ONCE", TEN, limits=UsageLimits(per_customer=1))
    store = InMemoryRedemptionStore()

    outcomes = run_in_parallel(
        lambda _: redeem(
            promo, store, eur("50.00"), customer="alice", now=START, attempts=PATIENT
        ),
        12,
    )
    receipts, failures = split(outcomes)

    assert len(receipts) == 1
    assert receipts[0].usage == UsageCounts(1, 1)
    assert store.usage_for("ONCE", customer="alice") == UsageCounts(1, 1)
    assert {failure.rejection for failure in failures} == {
        Rejection.CUSTOMER_LIMIT_REACHED
    }


def test_distinct_customers_do_not_block_each_other():
    promo = PromoCode("EACHONCE", TEN, limits=UsageLimits(per_customer=1))
    store = InMemoryRedemptionStore()
    workers = 10

    outcomes = run_in_parallel(
        lambda index: redeem(
            promo,
            store,
            eur("50.00"),
            customer=f"buyer-{index}",
            now=START,
            attempts=PATIENT,
        ),
        workers,
    )
    receipts, failures = split(outcomes)

    assert failures == []
    assert len(receipts) == workers
    assert {receipt.customer for receipt in receipts} == {
        f"buyer-{index}" for index in range(workers)
    }
    assert all(receipt.usage.for_customer == 1 for receipt in receipts)
    assert store.usage_for("EACHONCE") == UsageCounts(workers, 0)


def test_a_redelivered_webhook_racing_itself_never_spends_twice():
    promo = PromoCode("EVT", TEN, limits=UsageLimits(total=5))
    store = InMemoryRedemptionStore()

    outcomes = run_in_parallel(
        lambda _: redeem(
            promo,
            store,
            eur("80.00"),
            now=START,
            idempotency_key="evt_1",
            attempts=PATIENT,
        ),
        10,
    )
    receipts, failures = split(outcomes)

    assert failures == []
    assert len(receipts) == 10
    assert sum(1 for receipt in receipts if not receipt.replayed) == 1
    assert {receipt.usage for receipt in receipts} == {UsageCounts(1, 0)}
    assert store.usage_for("EVT") == UsageCounts(1, 0)


def test_a_replay_outlives_the_per_customer_cap_it_spent():
    promo = PromoCode("ONCE", TEN, limits=UsageLimits(per_customer=1))
    store = InMemoryRedemptionStore()

    first = redeem(
        promo,
        store,
        eur("50.00"),
        customer="alice",
        now=START,
        idempotency_key="evt_1",
    )
    again = redeem(
        promo,
        store,
        eur("50.00"),
        customer="alice",
        now=START,
        idempotency_key="evt_1",
    )

    assert first.replayed is False
    assert again.replayed is True
    assert again.usage == first.usage == UsageCounts(1, 1)
    assert again.discount == first.discount
    assert again.total == first.total
    assert store.usage_for("ONCE", customer="alice") == UsageCounts(1, 1)


def test_a_replay_outlives_the_global_cap_it_spent():
    promo = PromoCode(
        "LASTONE", FixedDiscount(eur("5.00")), limits=UsageLimits(total=1)
    )
    store = InMemoryRedemptionStore()

    first = redeem(promo, store, eur("40.00"), now=START, idempotency_key="evt_1")
    again = redeem(promo, store, eur("40.00"), now=START, idempotency_key="evt_1")

    assert again.replayed is True
    assert again.usage == first.usage == UsageCounts(1, 0)
    assert again.total == eur("35.00")
    assert store.usage_for("LASTONE") == UsageCounts(1, 0)


def test_a_replay_outlives_the_window_closing():
    ends_at = START + timedelta(hours=1)
    promo = PromoCode(
        "FLASH",
        TEN,
        window=ValidityWindow(START, ends_at),
        limits=UsageLimits(total=1, per_customer=1),
    )
    store = InMemoryRedemptionStore()

    first = redeem(
        promo,
        store,
        eur("80.00"),
        customer="alice",
        now=START,
        idempotency_key="evt_1",
    )
    again = redeem(
        promo,
        store,
        eur("80.00"),
        customer="alice",
        now=ends_at + timedelta(days=1),
        idempotency_key="evt_1",
    )

    assert again.replayed is True
    assert again.usage == first.usage == UsageCounts(1, 1)
    assert store.usage_for("FLASH", customer="alice") == UsageCounts(1, 1)


def test_a_different_key_is_a_new_redemption_and_meets_the_rules():
    promo = PromoCode("ONCE", TEN, limits=UsageLimits(per_customer=1))
    store = InMemoryRedemptionStore()

    redeem(
        promo,
        store,
        eur("50.00"),
        customer="alice",
        now=START,
        idempotency_key="evt_1",
    )

    with pytest.raises(PromoCodeRejected) as refused:
        redeem(
            promo,
            store,
            eur("50.00"),
            customer="alice",
            now=START,
            idempotency_key="evt_2",
        )

    assert refused.value.rejection is Rejection.CUSTOMER_LIMIT_REACHED
    assert store.usage_for("ONCE", customer="alice") == UsageCounts(1, 1)


def test_codes_do_not_share_counters_under_load():
    left = PromoCode("LEFT", TEN, limits=UsageLimits(total=1))
    right = PromoCode("RIGHT", TEN, limits=UsageLimits(total=1))
    store = InMemoryRedemptionStore()

    outcomes = run_in_parallel(
        lambda index: redeem(
            left if index % 2 == 0 else right,
            store,
            eur("40.00"),
            now=START,
            attempts=PATIENT,
        ),
        12,
    )
    receipts, _ = split(outcomes)

    assert sorted(receipt.code for receipt in receipts) == ["LEFT", "RIGHT"]
    assert store.usage_for("LEFT") == UsageCounts(1, 0)
    assert store.usage_for("RIGHT") == UsageCounts(1, 0)


class SneakyStore:
    """Lets a competitor commit in the gap between one read and its write."""

    def __init__(
        self,
        inner: InMemoryRedemptionStore,
        competitor: PromoCode,
        total: Money,
    ) -> None:
        self.inner = inner
        self.reads = 0
        self.interfered = False
        self._competitor = competitor
        self._total = total

    def usage_for(self, code: str, *, customer: Optional[str] = None) -> UsageCounts:
        self.reads += 1
        return self.inner.usage_for(code, customer=customer)

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Optional[Reservation]:
        return self.inner.replay_for(code, idempotency_key=idempotency_key)

    def record(
        self,
        code: str,
        *,
        customer: Optional[str] = None,
        expected: UsageCounts,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Reservation]:
        if not self.interfered:
            self.interfered = True
            redeem(self._competitor, self.inner, self._total, now=START)
        return self.inner.record(
            code,
            customer=customer,
            expected=expected,
            idempotency_key=idempotency_key,
        )


def test_a_stale_read_is_refused_and_the_rules_run_again():
    promo = PromoCode("LASTONE", TEN, limits=UsageLimits(total=1))
    store = SneakyStore(InMemoryRedemptionStore(), promo, eur("40.00"))

    with pytest.raises(PromoCodeRejected) as refused:
        redeem(promo, store, eur("40.00"), now=START)

    assert refused.value.rejection is Rejection.EXHAUSTED
    assert store.inner.usage_for("LASTONE") == UsageCounts(1, 0)


def test_a_stale_read_retries_against_fresh_counts_when_room_is_left():
    promo = PromoCode("CAP2", TEN, limits=UsageLimits(total=2))
    store = SneakyStore(InMemoryRedemptionStore(), promo, eur("80.00"))

    receipt = redeem(promo, store, eur("80.00"), now=START)

    assert receipt.usage == UsageCounts(2, 0)
    assert store.reads == 2
    assert store.inner.usage_for("CAP2") == UsageCounts(2, 0)


class NeverCommittingStore:
    """A store whose counters always move under the caller."""

    def __init__(self) -> None:
        self.writes = 0

    def usage_for(self, code: str, *, customer: Optional[str] = None) -> UsageCounts:
        return UsageCounts()

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Optional[Reservation]:
        return None

    def record(
        self,
        code: str,
        *,
        customer: Optional[str] = None,
        expected: UsageCounts,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Reservation]:
        self.writes += 1
        return None


@pytest.mark.parametrize("attempts", [1, 3, 7])
def test_endless_contention_raises_a_conflict_after_the_attempts(attempts: int):
    promo = PromoCode("BUSY", TEN)
    store = NeverCommittingStore()

    with pytest.raises(RedemptionConflict) as conflict:
        redeem(promo, store, eur("80.00"), now=START, attempts=attempts)

    assert conflict.value.code == "BUSY"
    assert conflict.value.attempts == attempts
    assert store.writes == attempts


class FlakyStore:
    """Refuses the first ``refusals`` writes, then behaves normally."""

    def __init__(self, inner: InMemoryRedemptionStore, refusals: int) -> None:
        self.inner = inner
        self.refusals = refusals

    def usage_for(self, code: str, *, customer: Optional[str] = None) -> UsageCounts:
        return self.inner.usage_for(code, customer=customer)

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Optional[Reservation]:
        return self.inner.replay_for(code, idempotency_key=idempotency_key)

    def record(
        self,
        code: str,
        *,
        customer: Optional[str] = None,
        expected: UsageCounts,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Reservation]:
        if self.refusals > 0:
            self.refusals -= 1
            return None
        return self.inner.record(
            code,
            customer=customer,
            expected=expected,
            idempotency_key=idempotency_key,
        )


def test_contention_within_the_budget_still_redeems():
    promo = PromoCode("CAP5", TEN, limits=UsageLimits(total=5))
    store = FlakyStore(InMemoryRedemptionStore(), refusals=2)

    receipt = redeem(promo, store, eur("80.00"), now=START, attempts=3)

    assert receipt.usage == UsageCounts(1, 0)
    assert store.inner.usage_for("CAP5") == UsageCounts(1, 0)


def test_a_store_returning_the_wrong_type_is_a_programming_error():
    class BadStore:
        def usage_for(self, code, *, customer=None):
            return (0, 0)

        def replay_for(self, code, *, idempotency_key):
            return None

        def record(self, code, *, customer=None, expected, idempotency_key=None):
            raise AssertionError("record must not be reached")

    with pytest.raises(TypeError):
        redeem(PromoCode("OPEN", TEN), BadStore(), eur("80.00"), now=START)


def test_a_store_replaying_the_wrong_type_is_a_programming_error():
    class BadStore:
        def usage_for(self, code, *, customer=None):
            return UsageCounts()

        def replay_for(self, code, *, idempotency_key):
            return UsageCounts(1, 0)

        def record(self, code, *, customer=None, expected, idempotency_key=None):
            raise AssertionError("record must not be reached")

    with pytest.raises(TypeError):
        redeem(
            PromoCode("OPEN", TEN),
            BadStore(),
            eur("80.00"),
            now=START,
            idempotency_key="evt_1",
        )


@pytest.mark.parametrize(
    "call,error",
    [
        (lambda promo, store: redeem("OPEN", store, eur("80.00")), TypeError),
        (lambda promo, store: redeem(promo, store, Decimal("80.00")), TypeError),
        (lambda promo, store: redeem(promo, store, eur("80.00"), attempts=0), ValueError),
        (
            lambda promo, store: redeem(promo, store, eur("80.00"), attempts=True),
            TypeError,
        ),
        (
            lambda promo, store: redeem(promo, store, eur("80.00"), idempotency_key="  "),
            ValueError,
        ),
        (
            lambda promo, store: redeem(promo, store, eur("80.00"), idempotency_key=7),
            TypeError,
        ),
        (
            lambda promo, store: redeem(promo, store, eur("80.00"), customer="  "),
            ValueError,
        ),
    ],
    ids=[
        "a code that is not a PromoCode",
        "a total that is not Money",
        "zero attempts",
        "a boolean attempt count",
        "a blank idempotency key",
        "an idempotency key that is not a string",
        "a blank customer",
    ],
)
def test_redeem_validates_its_arguments(call, error):
    promo = PromoCode("OPEN", TEN)
    store = InMemoryRedemptionStore()

    with pytest.raises(error):
        call(promo, store)


def test_the_reference_store_satisfies_the_protocol():
    assert isinstance(InMemoryRedemptionStore(), RedemptionStore)


def test_the_reference_store_refuses_a_write_against_stale_counts():
    store = InMemoryRedemptionStore()

    first = store.record("SAVE10", customer="alice", expected=UsageCounts(0, 0))
    stale = store.record("SAVE10", customer="alice", expected=UsageCounts(0, 0))

    assert isinstance(first, Reservation)
    assert first.usage == UsageCounts(1, 1)
    assert first.replayed is False
    assert stale is None
    assert store.usage_for("SAVE10", customer="alice") == UsageCounts(1, 1)


def test_the_reference_store_normalizes_codes_and_customers():
    store = InMemoryRedemptionStore()

    store.record("  save10 ", customer=" alice ", expected=UsageCounts(0, 0))

    assert store.usage_for("SAVE10", customer="alice") == UsageCounts(1, 1)
    assert store.usage_for("save10", customer="  alice") == UsageCounts(1, 1)
    assert store.usage_for("SAVE10") == UsageCounts(1, 0)
    assert store.usage_for("SAVE10", customer="bob") == UsageCounts(1, 0)


def test_the_reference_store_replays_a_known_key_without_incrementing():
    store = InMemoryRedemptionStore()

    first = store.record("SAVE10", expected=UsageCounts(0, 0), idempotency_key="evt_1")
    again = store.record("SAVE10", expected=UsageCounts(1, 0), idempotency_key="evt_1")

    assert first.replayed is False
    assert again.replayed is True
    assert again.usage == first.usage
    assert store.usage_for("SAVE10") == UsageCounts(1, 0)


def test_the_reference_store_looks_up_a_recorded_key():
    store = InMemoryRedemptionStore()

    assert store.replay_for("SAVE10", idempotency_key="evt_1") is None

    store.record(
        "SAVE10", customer="alice", expected=UsageCounts(0, 0), idempotency_key="evt_1"
    )

    assert store.replay_for("  save10 ", idempotency_key="evt_1") == Reservation(
        UsageCounts(1, 1), replayed=True
    )
    assert store.replay_for("SAVE10", idempotency_key="evt_2") is None
    assert store.replay_for("OTHER", idempotency_key="evt_1") is None
    assert store.usage_for("SAVE10", customer="alice") == UsageCounts(1, 1)


def test_the_reference_store_rejects_counts_it_cannot_compare():
    store = InMemoryRedemptionStore()

    with pytest.raises(TypeError):
        store.record("SAVE10", expected=(0, 0))
    with pytest.raises(ValueError):
        store.usage_for("   ")
