from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice


def test_totals_missing_389():
    xml_path = Path("tests/sg20_doc_discount.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert ok

    doc_discount = -df[df["sifra_dobavitelja"] == "_DOC_"]["vrednost"].sum()
    lines = df[df["sifra_dobavitelja"] != "_DOC_"]
    lines = lines[lines["rabata_pct"] < Decimal("99.5")]
    line_total = lines["vrednost"].sum()

    header_net = df["vrednost"].sum()
    assert line_total - doc_discount == header_net
