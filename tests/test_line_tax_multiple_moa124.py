from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_tax, NS


def test_line_tax_sums_multiple_entries(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>1.10</D_5004></C_C516></S_MOA>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>0.10</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>11.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "invoice.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 1

    root = ET.parse(xml_path).getroot()
    sg26 = root.findall(".//e:G_SG26", NS)[0]
    tax = _line_tax(sg26)[0]
    assert tax == Decimal("1.20")
