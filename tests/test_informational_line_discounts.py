from decimal import Decimal
from lxml import etree
from wsm.parsing.eslog import parse_invoice_totals


def test_informational_line_discounts_align_with_header():
    tree = etree.parse("tests/data/PR6167-Slika2.XML")
    t = parse_invoice_totals(tree)
    assert t["net"] == Decimal("843.40")
    assert t["vat"] == Decimal("185.55")
    assert t["gross"] == Decimal("1028.95")
    assert not t["mismatch"]
