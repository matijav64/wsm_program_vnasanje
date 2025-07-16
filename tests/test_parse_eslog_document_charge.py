from decimal import Decimal
from pathlib import Path
from wsm.parsing.eslog import parse_eslog_invoice, extract_header_net


def test_parse_eslog_invoice_handles_doc_charge():
    xml_path = Path("tests/minimal_doc_charge.xml")
    df, ok = parse_eslog_invoice(xml_path)

    charge_rows = df[df["sifra_dobavitelja"] == "_DOC_CHARGE_"]
    assert not charge_rows.empty
    charge_value = charge_rows.iloc[0]["vrednost"]

    header_total = extract_header_net(xml_path)
    lines_total = df["vrednost"].sum()

    assert charge_value == Decimal("1")
    assert lines_total == header_total
    assert ok
