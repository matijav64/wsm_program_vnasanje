from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    _line_tax,
    _tax_rate_from_header,
    extract_grand_total,
    NS,
)


def test_header_vat_rate():
    xml_path = Path('tests/header_vat_rate_multi.xml')
    df, ok = parse_eslog_invoice(xml_path)
    assert ok

    root = ET.parse(xml_path).getroot()
    lines = root.findall('.//e:G_SG26', NS)
    taxes = [_line_tax(sg) for sg in lines]
    assert sum(taxes) == Decimal('0')

    net_total = df['vrednost'].sum()
    rate = _tax_rate_from_header(root)
    expected_tax = (net_total * rate).quantize(Decimal('0.01'))

    grand_total = extract_grand_total(xml_path)
    assert grand_total - net_total == expected_tax
    assert grand_total == net_total + expected_tax
