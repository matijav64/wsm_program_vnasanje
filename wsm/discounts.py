def calculate_discounts(items):
    total = 0.0
    total_discount = 0.0

    for item in items:
        price = float(item['cena'])
        qty = float(item['kolicina'])
        discount = float(item.get('rabata', 0))

        total_item_value = (price * qty) - discount
        total += total_item_value
        total_discount += discount

    return total, total_discount