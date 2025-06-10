from decimal import Decimal

from wsm.discounts import calculate_discounts


def test_calculate_discounts_with_doc_discount():
    items = [
        {"cena": Decimal("10"), "kolicina": 2, "rabata": Decimal("1")},
        {"cena": Decimal("5"), "kolicina": 1, "rabata": Decimal("0")},
    ]

    total, discount = calculate_discounts(items, doc_discount=Decimal("3"))

    assert total == Decimal("21")
    assert discount == Decimal("4")

