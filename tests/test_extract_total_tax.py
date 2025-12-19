from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import extract_total_tax


def _compute_expected(xml_path: Path) -> Decimal:
    NS = {"e": "urn:eslog:2.00"}
    root = ET.parse(xml_path).getroot()
    total = Decimal("0")
    for sg52 in root.findall(".//e:G_SG52", NS):
        values = {}
        for moa in sg52.findall("./e:S_MOA", NS):
            code = moa.find("./e:C_C516/e:D_5025", NS)
            if code is not None and code.text in {"124", "125"}:
                val = moa.find("./e:C_C516/e:D_5004", NS)
                if val is not None:
                    values[code.text] = Decimal((val.text or "0").replace(",", "."))
        if len(values) >= 2:
            base = values.get("125")
            tax = values.get("124")
            if base is None or tax is None:
                continue
            if abs(tax) > abs(base):
                base, tax = tax, base
            total += tax
        elif "124" in values:
            total += values["124"]
    return total.quantize(Decimal("0.01"))


def test_extract_total_tax_single_rate():
    xml = Path("tests/PR5691-Slika2.XML")
    assert extract_total_tax(xml) == _compute_expected(xml)


def test_extract_total_tax_multiple_rates():
    xml = Path("tests/2025-581-racun.xml")
    assert extract_total_tax(xml) == _compute_expected(xml)


def test_extract_total_tax_swapped_base_and_tax(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>10.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "swapped.xml"
    path.write_text(xml)

    assert extract_total_tax(path) == Decimal("10.00")


def test_extract_total_tax_swapped_negative_credit(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>-100.00</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>-22.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "swapped_credit.xml"
    path.write_text(xml)

    assert extract_total_tax(path) == Decimal("-22.00")


def test_extract_total_tax_ignores_base_only(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>123.45</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "base_only.xml"
    path.write_text(xml)

    assert extract_total_tax(path) == Decimal("0.00")
