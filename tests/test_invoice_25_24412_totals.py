from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_tax, NS


def test_invoice_25_24412_totals():
    xml_path = Path("tests/25-24412.xml")
    df, ok = parse_eslog_invoice(xml_path)

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    taxes = [_line_tax(sg)[0] for sg in lines]
    tax_total = sum(taxes)

    grand_total = df["vrednost"].sum() + tax_total

    assert tax_total == Decimal("188.94")
    assert grand_total == Decimal("1951.11")
    assert ok
