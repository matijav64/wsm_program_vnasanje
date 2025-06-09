"""Utilities for calculating invoice totals and discounts.

Each element of ``items`` should be a mapping (e.g. ``dict`` or
``pandas.Series``) containing at least the following keys:

  - ``cena``     – unit price
  - ``kolicina`` – quantity
  - ``rabata``   – line level discount (optional, defaults to ``0``)

The :func:`calculate_discounts` function returns a tuple of ``Decimal`` values:
``(total_value, total_discount)``.
"""
from decimal import Decimal


def calculate_discounts(items):
    """Compute totals from invoice line items.

    Parameters
    ----------
    items : Iterable[Mapping]
        Collection of line items.  Each mapping must provide ``cena`` and
        ``kolicina`` values and may provide ``rabata`` for the line discount.

    Returns
    -------
    tuple[Decimal, Decimal]
        ``(total_value, total_discount)`` where ``total_value`` is the sum of
        item values after discounts and ``total_discount`` is the aggregated
        discount amount.
    """
    total = Decimal("0")
    total_discount = Decimal("0")

    for item in items:
        price = Decimal(str(item['cena']))
        qty = Decimal(str(item['kolicina']))
        discount = Decimal(str(item.get('rabata', 0)))

        total_item_value = (price * qty) - discount
        total += total_item_value
        total_discount += discount

    return total, total_discount

