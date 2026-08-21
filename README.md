# promocodes

Promotion engine: fixed and percentage discounts, validity windows, usage
limits, per-customer codes and atomic redemption.

## Status

Pre-alpha. Discount kinds and validity rules are implemented; atomic
redemption is not yet.

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

A code carries a half-open time window, a global cap, a per-customer cap and
an audience. `check()` reports the first rule that stops a redemption, or
`None` when the code may be used.

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
```

Timestamps must be timezone-aware; `check()` defaults to the current UTC time.
`validate()` raises `PromoCodeRejected` instead of returning a reason.

## Development

```bash
pip install -e .
```

## License

MIT

Maintained by [Shipmind Labs](https://shipmindlabs.com).
