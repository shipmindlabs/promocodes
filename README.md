# promocodes

Promotion engine: fixed and percentage discounts, validity windows, usage
limits, per-customer codes and atomic redemption.

Two mistakes turn a promotion engine into a refund machine: a discount that
grows larger than the basket, and a counter that two webhooks increment at the
same time. Both are spelled out below, together with what this package does
about them.

## Status

Pre-alpha. Discount kinds, validity rules, typed rejection reasons, atomic
redemption and capture-time re-validation are implemented.

## Installation

```bash
pip install promocodes
```

## Quickstart

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from promocodes import (
    InMemoryRedemptionStore,
    Money,
    PercentageDiscount,
    PromoCode,
    UsageCounts,
    UsageLimits,
    ValidityWindow,
    capture,
    quote,
)

start = datetime(2026, 1, 1, tzinfo=timezone.utc)
welcome = PromoCode(
    "welcome10",
    PercentageDiscount(Decimal("10")),
    window=ValidityWindow(start, start + timedelta(days=30)),
    limits=UsageLimits(total=1000, per_customer=1),
)

store = InMemoryRedemptionStore()
basket = Money(Decimal("80.00"), "EUR")
checkout = start + timedelta(days=3)

# At checkout: price the basket, or learn why the code cannot be used.
cart = quote(welcome, basket, customer="alice", now=checkout)
cart.discount   # 8.00 EUR
cart.payable    # 72.00 EUR -> what the payment intent is created for

# When the payment confirms: run every rule again, then spend the code.
receipt = capture(welcome, store, cart, now=checkout + timedelta(minutes=2))
receipt.total   # 72.00 EUR
receipt.usage   # UsageCounts(total=1, for_customer=1)
```

`check()` asks the same question without pricing anything and answers with the
reason instead of raising:

```python
welcome.check(customer="alice", usage=UsageCounts(12), now=checkout)
# None -> usable
welcome.check(customer="alice", usage=UsageCounts(12, for_customer=1), now=checkout)
# Rejection.CUSTOMER_LIMIT_REACHED
```

## Rule reference

A `PromoCode` is a code, the discount it grants, and four optional rules that
limit its use.

| rule | given as | refuses with |
| --- | --- | --- |
| time window | `window=ValidityWindow(starts_at, ends_at)` | `not_started`, `expired` |
| usage caps | `limits=UsageLimits(total=, per_customer=)` | `exhausted`, `customer_limit_reached` |
| audience | `audience=Audience.assigned_to(...)` | `not_assigned` |
| order minimum | `minimum_total=Money(...)` | `below_minimum` |

### Discounts

```python
from promocodes import FixedDiscount, PercentageDiscount

total = Money(Decimal("49.99"), "EUR")

PercentageDiscount(Decimal("15")).compute(total)  # Money(Decimal('7.50'), 'EUR')
PercentageDiscount(Decimal("15")).apply(total)    # Money(Decimal('42.49'), 'EUR')
FixedDiscount(Money(Decimal("5.00"), "EUR")).apply(total)  # 44.99 EUR
```

`compute()` returns what is taken off, `apply()` what is left to pay.
Percentages are rounded half-up to the smallest unit the currency is settled
in, so JPY yields whole yen and EUR yields cents. A percentage outside 0 to
100, a negative fixed amount, or a discount in a different currency than the
total is a programming error and raises instead of being quietly reinterpreted.

### Validity window

`ValidityWindow(starts_at, ends_at)` is half-open: the code is live on the
first instant and dead on the last. Either end may be `None` for an
open-ended window. Timestamps must be timezone-aware, and `check()` defaults
to the current UTC time.

### Usage limits

`UsageLimits(total=, per_customer=)` caps how often the code may be redeemed
globally and by one customer. A per-customer cap can only be enforced against
a known customer, so a call without one is refused with `customer_required`
rather than silently treated as anonymous. The counters themselves come from
`UsageCounts(total, for_customer)` -- passed in by the caller for `check()`,
read from the store by `redeem()`.

### Audience

`Audience.public()` (the default) admits everyone; `Audience.assigned_to(...)`
and `Audience.of(...)` hand the code to named customers. Code strings are
compared case-insensitively and trimmed (`" welcome10 "` is `WELCOME10`);
customer identifiers are only trimmed, never case-folded, because they are
your keys and not ours.

```python
from promocodes import Audience

comeback = PromoCode(
    "COMEBACK",
    PercentageDiscount(Decimal("20")),
    audience=Audience.assigned_to("alice", "bob"),
)
comeback.check(customer="carol")  # Rejection.NOT_ASSIGNED
```

### Order minimum

```python
shipping = PromoCode(
    "SHIP5",
    FixedDiscount(Money(Decimal("5.00"), "EUR")),
    minimum_total=Money(Decimal("30.00"), "EUR"),
)
shipping.check(total=Money(Decimal("12.00"), "EUR"))  # Rejection.BELOW_MINIMUM
shipping.check()  # Rejection.TOTAL_REQUIRED -- a minimum needs a total
```

### Which rule speaks first

`check()` reports the first rule that stops the redemption, in this order, so
the answer is stable no matter how many rules a code carries:

1. `customer_required`
2. `not_assigned`
3. `not_started`
4. `expired`
5. `exhausted`
6. `customer_limit_reached`
7. `total_required`
8. `below_minimum`

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
| `terms_changed` | the discount changed between quote and capture (never from `check()`) |

`is_usable()` collapses the reason to a bool, while `validate()` and `apply()`
raise `PromoCodeRejected`, which carries the same reason on its `rejection`
attribute.

## Trap 1: negative totals

A five-euro-off code meets a four-euro basket, and arithmetic that nobody
watched hands the buyer a euro back. The same shape appears whenever a
percentage is computed in floats, or two discounts are subtracted one after
the other.

The fix is that a discount is clamped at both ends before it ever reaches an
order total: **never negative, never larger than the total it is taken off.**

```python
basket = Money(Decimal("5.00"), "EUR")
twenty_off = FixedDiscount(Money(Decimal("20.00"), "EUR"))

twenty_off.compute(basket)  # 5.00 EUR -- not 20.00
twenty_off.apply(basket)    # 0.00 EUR -- never -15.00
```

The clamp lives in `Discount.compute()`, so it holds for every discount kind,
including ones you add yourself: a subclass implements `_amount_for()` and
inherits the guarantee. Around it, the cases that cannot be clamped into
something sensible are refused loudly instead:

- a negative order total raises `ValueError` -- a basket that owes money is a
  bug upstream, not a discount to compute;
- a negative `FixedDiscount` or a percentage outside 0 to 100 raises at
  construction, so a broken campaign fails when it is defined rather than at
  the first checkout;
- a discount whose currency differs from the total raises instead of
  comparing numbers that mean different things.

`Money` holds a `Decimal` and an ISO 4217 code, and rounding to the currency's
minor unit is explicit (`quantize()`, half-up by default). No float ever
touches an amount, so 0.1 + 0.2 stays 0.30.

## Trap 2: non-atomic redemption

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
from promocodes import redeem

order = Money(Decimal("80.00"), "EUR")

receipt = redeem(welcome, store, order, customer="alice", now=checkout)
receipt.discount            # 8.00 EUR
receipt.total               # 72.00 EUR -> what is left to pay
receipt.usage               # UsageCounts(total=1, for_customer=1)

redeem(welcome, store, order, customer="alice", now=checkout)
# PromoCodeRejected: WELCOME10: the customer reached the per-customer limit
```

Pass the payment provider's event id as `idempotency_key` and a redelivered
webhook costs nothing. The key is looked up before any rule runs, so a replay
is answered with the redemption it already made instead of meeting the cap it
spent itself or a window that has closed since:

```python
paid = redeem(
    welcome, store, order, customer="bob", now=checkout, idempotency_key="evt_1"
)
again = redeem(
    welcome, store, order, customer="bob", now=checkout, idempotency_key="evt_1"
)

again.replayed              # True
again.usage == paid.usage   # True -- no counter moved
```

### The storage contract

`InMemoryRedemptionStore` is a reference implementation for tests and
single-process use; it holds the guarantee with a lock, which is correct
across threads but not across processes. In production, implement
`RedemptionStore` on top of your own database:

```python
class RedemptionStore(Protocol):
    def usage_for(self, code: str, *, customer: str | None) -> UsageCounts: ...

    def replay_for(
        self, code: str, *, idempotency_key: str
    ) -> Reservation | None: ...

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
checked against. `replay_for()` is the key lookup `redeem()` asks first: return
`Reservation(usage, replayed=True)` for a key you already stored and `None` for
one you have never seen. `record()` carries the guarantee and must compare and
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
another transaction -- does not satisfy the contract. Back `idempotency_key`
with a unique constraint written in the same transaction as the increment, so
two deliveries that both miss the lookup cannot spend the code twice; `record()`
then answers the loser with `Reservation(usage, replayed=True)` as well.

When contention outlasts the retries (`attempts`, three by default),
`redeem()` raises `RedemptionConflict`; that is a signal to retry the request
higher up, not a refusal of the code.

## The second check, at capture time

A cart is priced when the buyer builds it and charged when the payment
provider confirms -- seconds later, or an hour later if the issuer asks for a
challenge. In between, the window can close, the last redemption can be taken
by somebody else, or the basket can shrink below the minimum. Checking only at
checkout quietly grants discounts the rules no longer allow.

`quote()` prices the cart, `capture()` runs every rule again against the clock
and the counters of the moment the money moves:

```python
from promocodes import CaptureRejected

try:
    receipt = capture(welcome, store, cart, now=payment_confirmed_at)
except CaptureRejected as stale:
    stale.rejection   # Rejection.EXPIRED -> good at checkout, not any more
    stale.quote       # the quote that went stale
```

`CaptureRejected` is a `PromoCodeRejected`, so callers that only care about the
refusal keep working, while a checkout API that wants to say "this code expired
while you were paying" can catch the narrower type. Nothing is recorded when
capture refuses.

### The terms can change too

A quote and a code agree on the code string, and nothing else. A campaign
edited between checkout and confirmation -- ten percent turned into five, a
fixed amount raised -- would therefore charge an amount the buyer never saw.
So `capture()` recomputes the discount on the quoted total before spending
anything, and refuses a result other than the quoted one:

```python
edited = PromoCode("WELCOME10", PercentageDiscount(Decimal("5")))

capture(edited, store, cart, now=payment_confirmed_at)
# CaptureRejected: WELCOME10: the discount changed since the code was quoted
```

What is compared is the money, not the campaign: a discount rewritten into a
different kind that is worth exactly the same on this basket charges the buyer
what they agreed to and passes.

Pass the amount actually being charged as `total=` when the basket changed
since checkout: the discount is recomputed against it on the terms of the
moment, the quoted amount is no longer the reference, and a cart that fell
under `minimum_total` is refused rather than discounted. Capture spends the
code through `redeem()`, so the atomicity guarantee and `idempotency_key`
apply unchanged.

A quote is a frozen dataclass of plain values, so it can be stored next to the
payment intent; `cart.is_expired(now)` answers the common case without loading
the code.

## Development

```bash
pip install -e ".[test]"
python -m pytest
```

## License

MIT

Maintained by [Shipmind Labs](https://shipmindlabs.com).
