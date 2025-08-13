from decimal import Decimal
from lxml import etree

from wsm.parsing.eslog import parse_invoice_totals


def test_pr6159_slika2_totals():
    tree = etree.parse("tests/PR6159-Slika2.XML")
    t = parse_invoice_totals(tree)
    assert t["net"] == Decimal("-103.19")
    assert t["vat"] == Decimal("-22.70")
    assert t["gross"] == Decimal("-125.89")
    assert not t["mismatch"]
