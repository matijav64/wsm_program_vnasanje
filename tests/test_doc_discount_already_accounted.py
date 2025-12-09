from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice


def test_document_discount_not_recreated_when_totals_match():
    xml_path = Path("tests/doc_discount_summary_only.xml")

    df, ok = parse_eslog_invoice(xml_path)

    assert ok
    assert "_DOC_" not in set(df["sifra_dobavitelja"])
    assert df["vrednost"].sum() == Decimal("90.00")
    assert df["ddv"].sum() == Decimal("19.80")
