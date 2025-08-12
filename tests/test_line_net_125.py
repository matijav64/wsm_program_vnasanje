from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_net, _line_tax, NS


def test_line_net_and_tax_with_moa125():
    xml_path = Path("tests/line_moa125_only.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 2

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    nets = [_line_net(sg) for sg in lines]
    assert nets[0] == Decimal("10")
    assert nets[1] == Decimal("8")

    taxes = [_line_tax(sg)[0] for sg in lines]
    assert taxes[0] == Decimal("2.20")
    assert taxes[1] == Decimal("1.76")
