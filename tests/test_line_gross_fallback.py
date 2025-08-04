from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, _line_gross, NS


def test_line_gross_fallback_to_moa_38(tmp_path: Path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Artikel</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>38</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "      <G_SG34>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "moa38.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    assert ok
    assert len(df) == 1
    assert df["vrednost"].iloc[0] == Decimal("10")

    root = ET.parse(xml_path).getroot()
    sg26 = root.find(".//e:G_SG26", NS)
    assert _line_gross(sg26) == Decimal("12.20")

