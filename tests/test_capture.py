from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from promocodes import (
    CaptureRejected,
    FixedDiscount,
    InMemoryRedemptionStore,
    Money,
    PercentageDiscount,
    PromoCode,
    PromoCodeRejected,
    Quote,
    Rejection,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    capture,
    quote,
    redeem,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def test_quote_prices_the_cart():
    promo = PromoCode("save10", PercentageDiscount(Decimal("10")))

    cart = quote(promo, eur("80.00"), customer="alice", now=START)

    assert cart.code == "SAVE10"
    assert cart.customer == "alice"
    assert cart.discount == eur("8.00")
    assert cart.payable == eur("72.00")
    assert cart.quoted_at == START
    assert cart.expires_at is None


def test_quote_carries_the_window_end():
    ends_at = START + timedelta(hours=1)
    promo = PromoCode(
        "FLASH",
        PercentageDiscount(Decimal("20")),
        window=ValidityWindow(START, ends_at),
    )

    cart = quote(promo, eur("50.00"), now=START)

    assert cart.expires_at == ends_at
    assert cart.is_expired(START + timedelta(minutes=30)) is False
    assert cart.is_expired(ends_at) is True


def test_quote_refuses_a_code_that_is_already_dead():
    promo = PromoCode(
        "FLASH",
        PercentageDiscount(Decimal("20")),
        window=ValidityWindow(START, START + timedelta(hours=1)),
    )

    with pytest.raises(PromoCodeRejected) as refused:
        quote(promo, eur("50.00"), now=START + timedelta(days=1))

    assert refused.value.rejection is Rejection.EXPIRED


def test_capture_spends_a_quote_that_is_still_good():
    promo = PromoCode(
        "SAVE10",
        PercentageDiscount(Decimal("10")),
        limits=UsageLimits(total=5, per_customer=1),
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("80.00"), customer="alice", now=START)

    paid_at = START + timedelta(minutes=2)
    receipt = capture(promo, store, cart, now=paid_at)

    assert receipt.discount == eur("8.00")
    assert receipt.total == eur("72.00")
    assert receipt.usage == UsageCounts(1, 1)
    assert receipt.redeemed_at == paid_at


def test_capture_refuses_a_quote_whose_window_closed_meanwhile():
    promo = PromoCode(
        "FLASH",
        PercentageDiscount(Decimal("20")),
        window=ValidityWindow(START, START + timedelta(hours=1)),
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("50.00"), now=START)

    with pytest.raises(CaptureRejected) as refused:
        capture(promo, store, cart, now=START + timedelta(hours=2))

    assert refused.value.rejection is Rejection.EXPIRED
    assert refused.value.quote is cart
    assert isinstance(refused.value, PromoCodeRejected)
    assert store.usage_for("FLASH") == UsageCounts(0, 0)


def test_capture_refuses_a_quote_whose_cap_was_spent_meanwhile():
    promo = PromoCode(
        "LASTONE", FixedDiscount(eur("5.00")), limits=UsageLimits(total=1)
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("40.00"), now=START)

    redeem(promo, store, eur("40.00"), now=START)

    with pytest.raises(CaptureRejected) as refused:
        capture(promo, store, cart, now=START)

    assert refused.value.rejection is Rejection.EXHAUSTED
    assert store.usage_for("LASTONE") == UsageCounts(1, 0)


def test_capture_refuses_a_campaign_edited_since_the_quote():
    quoted_at_ten = PromoCode("SAVE10", PercentageDiscount(Decimal("10")))
    store = InMemoryRedemptionStore()
    cart = quote(quoted_at_ten, eur("80.00"), customer="alice", now=START)

    edited = PromoCode("SAVE10", PercentageDiscount(Decimal("5")))

    with pytest.raises(CaptureRejected) as refused:
        capture(edited, store, cart, now=START + timedelta(minutes=2))

    assert refused.value.rejection is Rejection.TERMS_CHANGED
    assert refused.value.quote is cart
    assert isinstance(refused.value, PromoCodeRejected)
    assert store.usage_for("SAVE10", customer="alice") == UsageCounts(0, 0)


def test_capture_refuses_a_fixed_amount_that_was_raised():
    store = InMemoryRedemptionStore()
    cart = quote(
        PromoCode("SHIP5", FixedDiscount(eur("5.00"))), eur("40.00"), now=START
    )

    edited = PromoCode("SHIP5", FixedDiscount(eur("12.00")))

    with pytest.raises(CaptureRejected) as refused:
        capture(edited, store, cart, now=START)

    assert refused.value.rejection is Rejection.TERMS_CHANGED
    assert store.usage_for("SHIP5") == UsageCounts(0, 0)


def test_a_discount_worth_the_same_amount_is_not_a_change():
    store = InMemoryRedemptionStore()
    cart = quote(
        PromoCode("SAVE10", PercentageDiscount(Decimal("10"))), eur("80.00"), now=START
    )

    rewritten = PromoCode("SAVE10", FixedDiscount(eur("8.00")))
    receipt = capture(rewritten, store, cart, now=START)

    assert receipt.discount == cart.discount
    assert receipt.total == cart.payable


def test_an_explicit_total_charges_the_terms_of_the_moment():
    store = InMemoryRedemptionStore()
    cart = quote(
        PromoCode("SAVE10", PercentageDiscount(Decimal("10"))), eur("80.00"), now=START
    )

    edited = PromoCode("SAVE10", PercentageDiscount(Decimal("5")))
    receipt = capture(edited, store, cart, total=eur("80.00"), now=START)

    assert receipt.discount == eur("4.00")
    assert receipt.total == eur("76.00")


def test_capture_reprices_the_amount_actually_charged():
    promo = PromoCode("HALF", PercentageDiscount(Decimal("50")))
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("100.00"), now=START)

    receipt = capture(promo, store, cart, total=eur("40.00"), now=START)

    assert receipt.discount == eur("20.00")
    assert receipt.total == eur("20.00")


def test_capture_refuses_a_basket_that_fell_below_the_minimum():
    promo = PromoCode(
        "SHIP5", FixedDiscount(eur("5.00")), minimum_total=eur("30.00")
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("35.00"), now=START)

    with pytest.raises(CaptureRejected) as refused:
        capture(promo, store, cart, total=eur("20.00"), now=START)

    assert refused.value.rejection is Rejection.BELOW_MINIMUM
    assert store.usage_for("SHIP5") == UsageCounts(0, 0)


def test_capture_replays_a_redelivered_confirmation():
    promo = PromoCode(
        "SAVE10", PercentageDiscount(Decimal("10")), limits=UsageLimits(total=5)
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("80.00"), now=START)

    first = capture(promo, store, cart, now=START, idempotency_key="evt_1")
    again = capture(promo, store, cart, now=START, idempotency_key="evt_1")

    assert first.replayed is False
    assert again.replayed is True
    assert again.usage == first.usage
    assert store.usage_for("SAVE10") == UsageCounts(1, 0)


def test_capture_rejects_a_quote_for_another_code():
    promo = PromoCode("SAVE10", PercentageDiscount(Decimal("10")))
    other = PromoCode("SAVE20", PercentageDiscount(Decimal("20")))
    store = InMemoryRedemptionStore()
    cart = quote(other, eur("80.00"), now=START)

    with pytest.raises(ValueError):
        capture(promo, store, cart, now=START)


def test_capture_rejects_something_that_is_not_a_quote():
    promo = PromoCode("SAVE10", PercentageDiscount(Decimal("10")))
    store = InMemoryRedemptionStore()

    with pytest.raises(TypeError):
        capture(promo, store, eur("80.00"), now=START)


def test_a_quote_survives_a_round_trip_through_storage():
    promo = PromoCode(
        "SAVE10", PercentageDiscount(Decimal("10")), limits=UsageLimits(total=5)
    )
    store = InMemoryRedemptionStore()
    cart = quote(promo, eur("80.00"), customer="alice", now=START)

    restored = Quote(
        code=cart.code,
        customer=cart.customer,
        total=cart.total,
        discount=cart.discount,
        payable=cart.payable,
        quoted_at=cart.quoted_at,
        expires_at=cart.expires_at,
    )

    receipt = capture(promo, store, restored, now=START + timedelta(minutes=5))

    assert receipt.customer == "alice"
    assert receipt.total == cart.payable
