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


def test_extract_header_net_handles_doc_discount(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-5</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "disc.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("95.00")


def test_extract_header_net_handles_doc_charge(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>504</D_5025><D_5004>5</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "charge.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("105.00")


def test_extract_header_net_prefers_best_header_match(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>2</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>100.02</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "moa_mismatch.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("100.02")
