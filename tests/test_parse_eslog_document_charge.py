from decimal import Decimal
from pathlib import Path
from wsm.parsing.eslog import (
    parse_eslog_invoice,
    parse_invoice,
    extract_header_net,
)


def test_parse_eslog_invoice_handles_doc_charge():
    xml_path = Path("tests/minimal_doc_charge.xml")
    df, ok = parse_eslog_invoice(xml_path)

    charge_rows = df[df["sifra_dobavitelja"] == "DOC_CHG"]
    assert not charge_rows.empty
    charge_value = charge_rows.iloc[0]["vrednost"]

    header_total = extract_header_net(xml_path)
    lines_total = df[df["sifra_dobavitelja"] != "DOC_CHG"]["vrednost"].sum()

    assert charge_value == Decimal("1")
    assert (lines_total + charge_value).quantize(Decimal("0.01")) == header_total
    assert ok

    # parse_invoice should ignore document charges when computing discounts
    _, _, discount_total, _ = parse_invoice(xml_path)
    assert discount_total == Decimal("0.00")
