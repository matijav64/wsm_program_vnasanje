from decimal import Decimal
from pathlib import Path
from wsm.parsing.eslog import parse_invoice


def test_document_moa_204_discount():
    xml_path = Path("tests/minimal_doc_discount.xml")
    df, header_total, discount_total = parse_invoice(xml_path)
    assert header_total == Decimal("7.00")
    assert discount_total == Decimal("1.00")
