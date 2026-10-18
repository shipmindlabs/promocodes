from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

from promocodes import (
    Audience,
    FixedDiscount,
    Money,
    PercentageDiscount,
    PromoCode,
    PromoCodeRejected,
    Rejection,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    normalize_customer,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
ENDS = START + timedelta(hours=1)
BEFORE = START - timedelta(hours=1)
DURING = START + timedelta(minutes=30)
AFTER = START + timedelta(hours=2)


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


TEN = PercentageDiscount(Decimal("10"))

PUBLIC = PromoCode("OPEN", TEN)
WINDOWED = PromoCode("FLASH", TEN, window=ValidityWindow(START, ENDS))
CAPPED = PromoCode("CAP2", TEN, limits=UsageLimits(total=2))
PER_CUSTOMER = PromoCode("ONCE", TEN, limits=UsageLimits(per_customer=1))
BOTH_CAPS = PromoCode("BOTH", TEN, limits=UsageLimits(total=2, per_customer=1))
ASSIGNED = PromoCode("COMEBACK", TEN, audience=Audience.assigned_to("alice", "bob"))
MINIMUM = PromoCode("SHIP5", FixedDiscount(eur("5.00")), minimum_total=eur("30.00"))
ASSIGNED_WINDOWED = PromoCode(
    "VIP",
    TEN,
    window=ValidityWindow(START, ENDS),
    audience=Audience.assigned_to("alice"),
)
WINDOWED_CAPPED = PromoCode(
    "BURST", TEN, window=ValidityWindow(START, ENDS), limits=UsageLimits(total=1)
)
CUSTOMER_MINIMUM = PromoCode(
    "BIGSPEND",
    TEN,
    limits=UsageLimits(per_customer=1),
    minimum_total=eur("30.00"),
)


@dataclass(frozen=True)
class Case:
    name: str
    promo: PromoCode
    expected: Optional[Rejection] = None
    customer: Optional[str] = None
    usage: UsageCounts = UsageCounts()
    total: Optional[Money] = None
    now: datetime = DURING


CASES = [
    Case("a public code with no rules is usable", PUBLIC),
    Case("a public code admits a named customer", PUBLIC, customer="alice"),
    Case(
        "an uncapped code ignores the counters",
        PUBLIC,
        usage=UsageCounts(99, 0),
    ),
    Case("the window has not opened yet", WINDOWED, Rejection.NOT_STARTED, now=BEFORE),
    Case("the window opens on its first instant", WINDOWED, now=START),
    Case(
        "the window is still open one tick before it closes",
        WINDOWED,
        now=ENDS - timedelta(microseconds=1),
    ),
    Case(
        "the window is closed on its last instant",
        WINDOWED,
        Rejection.EXPIRED,
        now=ENDS,
    ),
    Case("the window closed long ago", WINDOWED, Rejection.EXPIRED, now=AFTER),
    Case("the global cap has room left", CAPPED, usage=UsageCounts(1, 0)),
    Case(
        "the global cap is reached",
        CAPPED,
        Rejection.EXHAUSTED,
        usage=UsageCounts(2, 0),
    ),
    Case(
        "the global cap was overshot",
        CAPPED,
        Rejection.EXHAUSTED,
        usage=UsageCounts(5, 0),
    ),
    Case(
        "a per-customer cap needs a customer",
        PER_CUSTOMER,
        Rejection.CUSTOMER_REQUIRED,
    ),
    Case(
        "this customer has not used the code yet",
        PER_CUSTOMER,
        customer="alice",
        usage=UsageCounts(7, 0),
    ),
    Case(
        "this customer reached the per-customer cap",
        PER_CUSTOMER,
        Rejection.CUSTOMER_LIMIT_REACHED,
        customer="alice",
        usage=UsageCounts(7, 1),
    ),
    Case("an assigned code needs a customer", ASSIGNED, Rejection.CUSTOMER_REQUIRED),
    Case("an assigned code admits its customer", ASSIGNED, customer="alice"),
    Case("an assigned code admits a padded identifier", ASSIGNED, customer="  bob "),
    Case(
        "an assigned code refuses a stranger",
        ASSIGNED,
        Rejection.NOT_ASSIGNED,
        customer="carol",
    ),
    Case("a minimum needs a total", MINIMUM, Rejection.TOTAL_REQUIRED),
    Case(
        "the total is under the minimum",
        MINIMUM,
        Rejection.BELOW_MINIMUM,
        total=eur("29.99"),
    ),
    Case("the total is exactly the minimum", MINIMUM, total=eur("30.00")),
    Case("the total is above the minimum", MINIMUM, total=eur("30.01")),
    Case(
        "a missing customer outranks a closed window",
        ASSIGNED_WINDOWED,
        Rejection.CUSTOMER_REQUIRED,
        now=AFTER,
    ),
    Case(
        "the audience outranks a closed window",
        ASSIGNED_WINDOWED,
        Rejection.NOT_ASSIGNED,
        customer="carol",
        now=AFTER,
    ),
    Case(
        "the window outranks the global cap",
        WINDOWED_CAPPED,
        Rejection.EXPIRED,
        usage=UsageCounts(9, 0),
        now=AFTER,
    ),
    Case(
        "the global cap outranks the per-customer cap",
        BOTH_CAPS,
        Rejection.EXHAUSTED,
        customer="alice",
        usage=UsageCounts(2, 1),
    ),
    Case(
        "the per-customer cap outranks the minimum",
        CUSTOMER_MINIMUM,
        Rejection.CUSTOMER_LIMIT_REACHED,
        customer="alice",
        usage=UsageCounts(3, 1),
        total=eur("5.00"),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_check_reports_the_first_rule_that_stops_a_code(case: Case):
    rejection = case.promo.check(
        customer=case.customer, usage=case.usage, total=case.total, now=case.now
    )

    assert rejection is case.expected


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_is_usable_and_validate_agree_with_check(case: Case):
    usable = case.promo.is_usable(
        customer=case.customer, usage=case.usage, total=case.total, now=case.now
    )
    assert usable is (case.expected is None)

    if case.expected is None:
        case.promo.validate(
            customer=case.customer, usage=case.usage, total=case.total, now=case.now
        )
        return

    with pytest.raises(PromoCodeRejected) as refused:
        case.promo.validate(
            customer=case.customer, usage=case.usage, total=case.total, now=case.now
        )

    assert refused.value.rejection is case.expected
    assert refused.value.code == case.promo.code
    assert case.expected.value in str(refused.value)


@pytest.mark.parametrize(
    "window,moment,contained",
    [
        (ValidityWindow.open(), BEFORE, True),
        (ValidityWindow.open(), AFTER, True),
        (ValidityWindow(START, None), BEFORE, False),
        (ValidityWindow(START, None), START, True),
        (ValidityWindow(START, None), AFTER, True),
        (ValidityWindow(None, ENDS), BEFORE, True),
        (ValidityWindow(None, ENDS), ENDS - timedelta(microseconds=1), True),
        (ValidityWindow(None, ENDS), ENDS, False),
        (ValidityWindow(START, ENDS), DURING, True),
        (ValidityWindow(START, ENDS), ENDS, False),
    ],
)
def test_a_window_is_half_open(window: ValidityWindow, moment: datetime, contained: bool):
    assert window.contains(moment) is contained


@pytest.mark.parametrize(
    "limits,counts,reached",
    [
        (UsageLimits.unlimited(), UsageCounts(999, 999), (False, False)),
        (UsageLimits(total=2), UsageCounts(1, 0), (False, False)),
        (UsageLimits(total=2), UsageCounts(2, 0), (True, False)),
        (UsageLimits(total=2), UsageCounts(3, 0), (True, False)),
        (UsageLimits(per_customer=1), UsageCounts(9, 0), (False, False)),
        (UsageLimits(per_customer=1), UsageCounts(9, 1), (False, True)),
        (UsageLimits(total=2, per_customer=1), UsageCounts(2, 1), (True, True)),
    ],
)
def test_limits_report_which_cap_is_reached(
    limits: UsageLimits, counts: UsageCounts, reached: tuple[bool, bool]
):
    assert (
        limits.total_reached(counts.total),
        limits.customer_reached(counts.for_customer),
    ) == reached


@pytest.mark.parametrize(
    "audience,customer,admitted",
    [
        (Audience.public(), None, True),
        (Audience.public(), "alice", True),
        (Audience.assigned_to("alice", "bob"), "alice", True),
        (Audience.assigned_to("alice", "bob"), "bob", True),
        (Audience.assigned_to("alice"), "  alice ", True),
        (Audience.assigned_to("alice"), "carol", False),
        (Audience.assigned_to("alice"), None, False),
        (Audience.of(["alice", "bob"]), "bob", True),
    ],
)
def test_an_audience_admits_only_its_customers(
    audience: Audience, customer: Optional[str], admitted: bool
):
    assert audience.admits(customer) is admitted


@pytest.mark.parametrize(
    "build,error",
    [
        (lambda: ValidityWindow(ENDS, START), ValueError),
        (lambda: ValidityWindow(START, START), ValueError),
        (lambda: ValidityWindow(datetime(2026, 1, 1)), ValueError),
        (lambda: ValidityWindow(None, datetime(2026, 1, 1)), ValueError),
        (lambda: ValidityWindow("2026-01-01"), TypeError),
        (lambda: UsageLimits(total=0), ValueError),
        (lambda: UsageLimits(per_customer=-1), ValueError),
        (lambda: UsageLimits(total=True), TypeError),
        (lambda: UsageLimits(per_customer="2"), TypeError),
        (lambda: UsageCounts(-1), ValueError),
        (lambda: UsageCounts(1, 2), ValueError),
        (lambda: UsageCounts(1.0), TypeError),
        (lambda: Audience("alice"), TypeError),
        (lambda: Audience(frozenset()), ValueError),
        (lambda: Audience(frozenset(["   "])), ValueError),
        (lambda: normalize_customer("  "), ValueError),
        (lambda: normalize_customer(None), TypeError),
    ],
    ids=[
        "window ends before it starts",
        "window ends when it starts",
        "naive start",
        "naive end",
        "start is not a datetime",
        "a cap of zero",
        "a negative cap",
        "a boolean cap",
        "a string cap",
        "a negative count",
        "more customer redemptions than total",
        "a float count",
        "an audience built from a bare string",
        "an audience without customers",
        "an audience of blank identifiers",
        "a blank customer",
        "a customer that is not a string",
    ],
)
def test_impossible_rules_are_refused_at_construction(build, error):
    with pytest.raises(error):
        build()


def test_a_minimum_in_another_currency_is_a_programming_error():
    with pytest.raises(ValueError):
        MINIMUM.check(total=Money(Decimal("50.00"), "USD"), now=DURING)


def test_check_rejects_arguments_of_the_wrong_type():
    with pytest.raises(TypeError):
        PUBLIC.check(usage=(0, 0), now=DURING)
    with pytest.raises(TypeError):
        PUBLIC.check(total=Decimal("10.00"), now=DURING)


def test_every_rejection_carries_a_sentence_and_a_slug():
    assert {rejection.slug for rejection in Rejection} == {
        "not_started",
        "expired",
        "exhausted",
        "customer_limit_reached",
        "not_assigned",
        "customer_required",
        "below_minimum",
        "total_required",
        "terms_changed",
    }
    assert all(rejection.value.strip() for rejection in Rejection)
