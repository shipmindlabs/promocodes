# promocodes

Promotion engine: fixed and percentage discounts, validity windows, usage
limits, per-customer codes and atomic redemption.

## Status

Pre-alpha. Discount kinds, validity rules, typed rejection reasons and atomic
redemption are implemented.

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

### Atomic redemption

`check()` only reads rules; spending a code is a write, and writes race. The
classic bug is a webhook doing `used_count += 1` and then `save()`: two
callbacks read the same number, both write it back, and a code capped at one
redemption is spent twice.

`redeem()` closes that gap. **A redemption is recorded only if the counters it
was checked against are still the ones in storage.** If they moved, the write
is refused, the counts are read again, the rules run again, and the caller
either redeems against fresh counts or gets a normal `PromoCodeRejected`. A
cap can therefore never be overspent, however many callbacks arrive at once.

```python
from promocodes import InMemoryRedemptionStore, redeem

store = InMemoryRedemptionStore()
order = Money(Decimal("80.00"), "EUR")

receipt = redeem(welcome, store, order, customer="alice", now=now)
receipt.discount            # 8.00 EUR
receipt.total               # 72.00 EUR -> what is left to pay
receipt.usage               # UsageCounts(total=1, for_customer=1)

redeem(welcome, store, order, customer="alice", now=now)
# PromoCodeRejected: WELCOME10: the customer reached the per-customer limit
```

Pass the payment provider's event id as `idempotency_key` and a redelivered
webhook costs nothing: the redemption is returned again with
`replayed=True` and no counter moves.

#### The storage protocol

`InMemoryRedemptionStore` is a reference implementation for tests and
single-process use. In production, implement `RedemptionStore` on top of your
own database:

```python
class RedemptionStore(Protocol):
    def usage_for(self, code: str, *, customer: str | None) -> UsageCounts: ...

    def record(
        self,
        code: str,
        *,
        customer: str | None,
        expected: UsageCounts,
        idempotency_key: str | None = None,
    ) -> Reservation | None: ...
```

`usage_for()` may return a stale read; it only produces the value the rules are
checked against. `record()` carries the guarantee and must compare and
increment in one atomic step:

```sql
UPDATE promo_usage
   SET total = total + 1,
       for_customer = for_customer + 1
 WHERE code = :code AND customer = :customer
   AND total = :expected_total
   AND for_customer = :expected_for_customer;
```

Zero affected rows means someone else redeemed first: return `None` and
`redeem()` retries. Reading in one statement and writing in another -- or in
another transaction -- does not satisfy the contract. Back
`idempotency_key` with a unique constraint written in the same transaction as
the increment, and return the stored counts as `Reservation(usage,
replayed=True)` when the key is already known.

When contention outlasts the retries (`attempts`, three by default),
`redeem()` raises `RedemptionConflict`; that is a signal to retry the request
higher up, not a refusal of the code.

## Development

```bash
pip install -e .
```

## License

MIT

Maintained by [Shipmind Labs](https://shipmindlabs.com).
