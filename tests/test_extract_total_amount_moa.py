from decimal import Decimal
import xml.etree.ElementTree as ET
from wsm.parsing.money import extract_total_amount


def test_extract_total_amount_moa_base_only():
    xml = (
        "<Invoice>"
        "  <S_MOA><C_C516><D_5025>79</D_5025><D_5004>123.45</D_5004></C_C516></S_MOA>"
        "</Invoice>"
    )
    root = ET.fromstring(xml)
    assert extract_total_amount(root) == Decimal("123.45")


def test_extract_total_amount_moa_with_discount():
    xml = (
        "<Invoice>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>200</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>176</D_5025><D_5004>-20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = ET.fromstring(xml)
    assert extract_total_amount(root) == Decimal("170.00")


def test_extract_total_amount_moa_with_discount_260_500():
    xml = (
        "<Invoice>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>200</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>260</D_5025><D_5004>-20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>500</D_5025><D_5004>-0.05</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = ET.fromstring(xml)
    assert extract_total_amount(root) == Decimal("179.95")
