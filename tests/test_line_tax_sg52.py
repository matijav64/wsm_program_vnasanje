from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_tax, NS


def test_line_tax_from_sg52():
    xml_path = Path("tests/line_tax_sg52.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 1

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    taxes = [_line_tax(sg) for sg in lines]
    assert taxes[0] == Decimal("2.20")
