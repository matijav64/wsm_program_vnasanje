from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_tax, NS


def test_line_tax_fallback_to_rate():
    xml_path = Path("tests/line_missing_moa124.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert len(df) == 2
    assert ok

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    taxes = [_line_tax(sg)[0] for sg in lines]
    assert taxes[0] == Decimal("2.20")
    assert taxes[1] == Decimal("1.76")
    assert sum(taxes) == Decimal("3.96")
