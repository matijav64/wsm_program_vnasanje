from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    _line_net,
    _line_net_before_discount,
    _line_tax,
    NS,
)


def test_line_net_and_tax_with_moa125():
    xml_path = Path("tests/line_moa125_only.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 2

    root = ET.parse(xml_path).getroot()
    lines = root.findall(".//e:G_SG26", NS)
    nets_after = [_line_net(sg) for sg in lines]
    nets_before = [_line_net_before_discount(sg) for sg in lines]
    assert nets_after[0] == Decimal("10")
    assert nets_after[1] == Decimal("8")
    assert nets_before[0] == Decimal("10")
    assert nets_before[1] == Decimal("10")

    taxes = [_line_tax(sg)[0] for sg in lines]
    assert taxes[0] == Decimal("2.20")
    assert taxes[1] == Decimal("1.76")

    # verify parser output prices before and after discount
    assert df.loc[0, "cena_bruto"] == Decimal("10")
    assert df.loc[0, "cena_netto"] == Decimal("10")
    assert df.loc[1, "cena_bruto"] == Decimal("5")
    assert df.loc[1, "cena_netto"] == Decimal("4")
