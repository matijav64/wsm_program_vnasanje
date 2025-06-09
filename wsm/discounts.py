from decimal import Decimal


def calculate_discounts(items):
    """Return total value and total discount for a list of invoice items."""
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

