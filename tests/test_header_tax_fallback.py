from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    _line_tax,
    extract_grand_total,
    NS,
)


def test_tax_total_fallback_from_header(tmp_path):
    xml_path = Path("tests/header_tax_rate.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 1
    assert df["vrednost"].sum() == Decimal("10")

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    assert _line_tax(lines[0])[0] == Decimal("0")

    grand_total = extract_grand_total(xml_path)
    assert grand_total - df["vrednost"].sum() == Decimal("2.20")
