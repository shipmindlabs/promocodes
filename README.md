# promocodes

Promotion engine: fixed and percentage discounts, validity windows, usage
limits, per-customer codes and atomic redemption.

## Status

Pre-alpha. Discount kinds are implemented; validity windows, usage limits and
redemption are not yet.

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

## Development

```bash
pip install -e .
```

## License

MIT

Maintained by [Shipmind Labs](https://shipmindlabs.com).
