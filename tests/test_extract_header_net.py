from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import extract_header_net


def test_extract_header_net_falls_back_to_moa_79(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>45.67</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "moa79.xml"
    xml_path.write_text(xml)
    assert extract_header_net(xml_path) == Decimal("45.67")
