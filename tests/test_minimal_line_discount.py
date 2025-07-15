from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice, extract_header_net


def test_minimal_line_discount():
    xml_path = Path("tests/minimal_line_discount.xml")
    df, ok = parse_eslog_invoice(xml_path)

    assert len(df) == 1
    line = df.iloc[0]
    assert line["vrednost"] == Decimal("15.00")

    header_total = extract_header_net(xml_path)
    assert df["vrednost"].sum().quantize(Decimal("0.01")) == header_total
    assert ok


def test_minimal_line_discount_no_namespace():
    xml_path = Path("tests/minimal_line_discount_no_ns.xml")
    df, ok = parse_eslog_invoice(xml_path)

    assert len(df) == 1
    line = df.iloc[0]
    assert line["vrednost"] == Decimal("15.00")

    header_total = extract_header_net(xml_path)
    assert df["vrednost"].sum().quantize(Decimal("0.01")) == header_total
    assert ok
