# promocodes

Promotion engine: fixed and percentage discounts, validity windows, usage
limits, per-customer codes and atomic redemption.

## Status

Pre-alpha. Discount kinds, validity rules and typed rejection reasons are
implemented; atomic redemption is not yet.

## Installation

```bash
pip install promocodes
```

## Usage

```python
from decimal import Decimal

from promocodes import FixedDiscount, Money, PercentageDiscount

total = Money(Decimal("49.99"), "EUR")

PercentageDiscount(Decimal("15")).compute(total)  # Money(Decimal('7.50'), 'EUR')
PercentageDiscount(Decimal("15")).apply(total)    # Money(Decimal('42.49'), 'EUR')

# A discount never exceeds the total and is never negative.
FixedDiscount(Money(Decimal("80.00"), "EUR")).compute(total)  # 49.99 EUR
```

Percentages are rounded half-up to the smallest unit of the currency, so JPY
yields whole yen and EUR yields cents.

### Validity

A code carries a half-open time window, a global cap, a per-customer cap, an
audience and an optional order minimum. `check()` reports the first rule that
stops a redemption, or `None` when the code may be used.

```python
from datetime import datetime, timedelta, timezone

from promocodes import (
    Audience,
    PercentageDiscount,
    PromoCode,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
)

start = datetime(2026, 1, 1, tzinfo=timezone.utc)
welcome = PromoCode(
    "welcome10",
    PercentageDiscount(Decimal("10")),
    window=ValidityWindow(start, start + timedelta(days=30)),
    limits=UsageLimits(total=1000, per_customer=1),
)

now = start + timedelta(days=3)
welcome.check(customer="alice", usage=UsageCounts(total=12), now=now)
# None -> usable
welcome.check(customer="alice", usage=UsageCounts(12, for_customer=1), now=now)
# Rejection.CUSTOMER_LIMIT_REACHED

# Codes can also be handed to named customers instead of the public.
comeback = PromoCode(
    "COMEBACK",
    PercentageDiscount(Decimal("20")),
    audience=Audience.assigned_to("alice", "bob"),
)
comeback.check(customer="carol")  # Rejection.NOT_ASSIGNED

# A code can also demand a basket of a certain size.
shipping = PromoCode(
    "SHIP5",
    FixedDiscount(Money(Decimal("5.00"), "EUR")),
    minimum_total=Money(Decimal("30.00"), "EUR"),
)
shipping.check(total=Money(Decimal("12.00"), "EUR"))  # Rejection.BELOW_MINIMUM
```

### Why a code was refused

Refusals are typed rather than a bare `False`, so a checkout API can tell the
buyer what went wrong. `Rejection.value` is the human sentence and
`Rejection.slug` the machine-readable key:

```python
rejection = shipping.check(total=Money(Decimal("12.00"), "EUR"))
{"error": rejection.slug, "detail": rejection.value}
# {'error': 'below_minimum',
#  'detail': 'the order total is below the minimum this code requires'}
```

| slug | reported when |
| --- | --- |
| `not_started` | the validity window opens later |
| `expired` | the validity window has closed |
| `exhausted` | the global usage cap is reached |
| `customer_limit_reached` | this customer used the code as often as allowed |
| `not_assigned` | the code belongs to other customers |
| `customer_required` | the rules need a customer and none was given |
| `below_minimum` | the order total is under `minimum_total` |
| `total_required` | the code has a minimum and no total was given |

Timestamps must be timezone-aware; `check()` defaults to the current UTC time.
`is_usable()` collapses the reason to a bool, while `validate()` and `apply()`
raise `PromoCodeRejected`, which carries the same reason on its `rejection`
attribute.

## Development

```bash
pip install -e .
```

## License

MIT

Maintained by [Shipmind Labs](https://shipmindlabs.com).
