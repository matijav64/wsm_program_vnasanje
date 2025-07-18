from decimal import Decimal
from pathlib import Path
from wsm.parsing.eslog import extract_header_gross


def test_extract_header_gross_reads_moa(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    p = tmp_path / "g.xml"
    p.write_text(xml)
    assert extract_header_gross(p) == Decimal("12.20")
