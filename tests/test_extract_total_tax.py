from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import extract_total_tax


def _compute_expected(xml_path: Path) -> Decimal:
    NS = {"e": "urn:eslog:2.00"}
    root = ET.parse(xml_path).getroot()
    total = Decimal("0")
    for sg52 in root.findall(".//e:G_SG52", NS):
        for moa in sg52.findall("./e:S_MOA", NS):
            code = moa.find("./e:C_C516/e:D_5025", NS)
            if code is not None and code.text == "124":
                val = moa.find("./e:C_C516/e:D_5004", NS)
                if val is not None:
                    total += Decimal((val.text or "0").replace(",", "."))
    return total.quantize(Decimal("0.01"))


def test_extract_total_tax_single_rate():
    xml = Path("tests/PR5691-Slika2.XML")
    assert extract_total_tax(xml) == _compute_expected(xml)


def test_extract_total_tax_multiple_rates():
    xml = Path("tests/2025-581-racun.xml")
    assert extract_total_tax(xml) == _compute_expected(xml)
