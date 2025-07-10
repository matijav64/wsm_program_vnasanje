from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_tax, NS


def test_vat_rounding_totals():
    xml_path = Path('tests/25-24412.xml')
    df, ok = parse_eslog_invoice(xml_path)

    root = ET.parse(xml_path).getroot()
    lines = root.findall('.//e:G_SG26', NS)
    taxes = [_line_tax(sg) for sg in lines]
    tax_total = sum(taxes)

    moa124_total = Decimal('0')
    for sg in lines:
        for moa in sg.findall('.//e:S_MOA', NS):
            if moa.find('./e:C_C516/e:D_5025', NS) is not None and moa.find('./e:C_C516/e:D_5025', NS).text == '124':
                val = Decimal((moa.find('./e:C_C516/e:D_5004', NS).text or '0').replace(',', '.'))
                moa124_total += val
    assert tax_total == moa124_total == Decimal('188.94')

    grand_total = df['vrednost'].sum() + tax_total
    assert abs(grand_total - Decimal('1951.11')) <= Decimal('0.01')
    assert ok
