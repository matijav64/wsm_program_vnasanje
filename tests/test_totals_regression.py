from decimal import Decimal
from lxml import etree

from wsm.parsing.eslog import parse_invoice_totals


def test_informational_discounts_totals_match():
    tree = etree.parse("tests/data/PR6167-Slika2.XML")
    t = parse_invoice_totals(tree)
    assert t["net"] == Decimal("843.40")
    assert t["vat"] == Decimal("185.55")
    assert t["gross"] == Decimal("1028.95")
    assert not t["mismatch"]


def test_credit_note_totals_match():
    tree = etree.parse("tests/data/credit_note.xml")
    t = parse_invoice_totals(tree)
    assert t["net"] == Decimal("-100")
    assert t["vat"] == Decimal("-22")
    assert t["gross"] == Decimal("-122")
    assert not t["mismatch"]
