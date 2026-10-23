from __future__ import annotations

from decimal import Decimal

import pytest

from promocodes import FixedDiscount, Money, PercentageDiscount


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def test_a_percentage_is_rounded_to_the_currency_unit():
    assert PercentageDiscount(Decimal("15")).compute(eur("49.99")) == eur("7.50")
    assert PercentageDiscount(Decimal("15")).apply(eur("49.99")) == eur("42.49")


def test_a_percentage_of_a_zero_decimal_currency_stays_whole():
    yen = Money(Decimal("1999"), "JPY")

    assert PercentageDiscount(Decimal("10")).compute(yen) == Money(
        Decimal("200"), "JPY"
    )


def test_a_discount_never_exceeds_the_total():
    assert FixedDiscount(eur("20.00")).compute(eur("5.00")) == eur("5.00")
    assert FixedDiscount(eur("20.00")).apply(eur("5.00")) == eur("0.00")


def test_a_capped_percentage_stops_at_the_cap():
    twenty = PercentageDiscount(Decimal("20"), up_to=eur("10.00"))

    assert twenty.compute(eur("100.00")) == eur("10.00")
    assert twenty.apply(eur("100.00")) == eur("90.00")


def test_a_capped_percentage_below_the_cap_is_the_percentage():
    twenty = PercentageDiscount(Decimal("20"), up_to=eur("10.00"))

    assert twenty.compute(eur("30.00")) == eur("6.00")
    assert twenty.apply(eur("30.00")) == eur("24.00")


def test_a_percentage_worth_exactly_its_cap_is_allowed():
    twenty = PercentageDiscount(Decimal("20"), up_to=eur("10.00"))

    assert twenty.compute(eur("50.00")) == eur("10.00")


def test_a_zero_cap_takes_nothing_off():
    capped = PercentageDiscount(Decimal("50"), up_to=Money.zero("EUR"))

    assert capped.compute(eur("80.00")) == eur("0.00")
    assert capped.apply(eur("80.00")) == eur("80.00")


def test_the_cap_does_not_lift_the_clamp_to_the_total():
    capped = PercentageDiscount(Decimal("100"), up_to=eur("50.00"))

    assert capped.compute(eur("3.00")) == eur("3.00")
    assert capped.apply(eur("3.00")) == eur("0.00")


def test_an_uncapped_percentage_is_unchanged():
    twenty = PercentageDiscount(Decimal("20"))

    assert twenty.up_to is None
    assert twenty.compute(eur("100.00")) == eur("20.00")


@pytest.mark.parametrize(
    "build,error",
    [
        (lambda: PercentageDiscount(Decimal("20"), up_to=eur("-1.00")), ValueError),
        (lambda: PercentageDiscount(Decimal("20"), up_to=Decimal("10.00")), TypeError),
        (lambda: PercentageDiscount(Decimal("101")), ValueError),
        (lambda: PercentageDiscount(Decimal("-1")), ValueError),
        (lambda: PercentageDiscount(None), TypeError),
        (lambda: FixedDiscount(eur("-5.00")), ValueError),
        (lambda: FixedDiscount(Decimal("5.00")), TypeError),
    ],
    ids=[
        "a negative cap",
        "a cap that is not Money",
        "a percentage above 100",
        "a negative percentage",
        "a percentage that is not a number",
        "a negative fixed amount",
        "a fixed amount that is not Money",
    ],
)
def test_impossible_discounts_are_refused_at_construction(build, error):
    with pytest.raises(error):
        build()


def test_a_cap_in_another_currency_is_refused_when_the_discount_is_computed():
    capped = PercentageDiscount(Decimal("20"), up_to=Money(Decimal("10.00"), "USD"))

    with pytest.raises(ValueError):
        capped.compute(eur("100.00"))


def test_a_fixed_discount_in_another_currency_is_refused():
    with pytest.raises(ValueError):
        FixedDiscount(Money(Decimal("5.00"), "USD")).compute(eur("40.00"))


def test_a_negative_total_is_a_programming_error():
    with pytest.raises(ValueError):
        PercentageDiscount(Decimal("10")).compute(eur("-1.00"))
