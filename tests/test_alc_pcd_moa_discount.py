from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import _alc_pcd_moa_discount


def _line(xml: str) -> LET._Element:
    return LET.fromstring(xml)


def test_alc_pcd_moa_discount_and_gratis() -> None:
    sg26_discount = _line(
        "<G_SG26>"
        "  <S_QTY><C_C186><D_6060>6</D_6060><D_6411>KGM</D_6411></C_C186></S_QTY>"
        "  <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>20.9517</D_5118></C_C509></S_PRI>"
        "  <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>29.2</D_5118></C_C509></S_PRI>"
        "  <G_SG39>"
        "    <S_ALC><D_5463>A</D_5463></S_ALC>"
        "    <G_SG41><S_PCD><C_C501><D_5245>1</D_5245><D_5482>28.25</D_5482></C_C501></S_PCD></G_SG41>"
        "    <G_SG42><S_MOA><C_C516><D_5025>204</D_5025><D_5004>49.49</D_5004></C_C516></S_MOA></G_SG42>"
        "  </G_SG39>"
        "</G_SG26>"
    )
    pct, amt, gratis = _alc_pcd_moa_discount(sg26_discount, Decimal("6"))
    assert pct == Decimal("28.25")
    assert amt == Decimal("49.49")
    assert not gratis

    sg26_gratis = _line(
        "<G_SG26>"
        "  <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "  <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>"
        "  <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "  <G_SG39>"
        "    <S_ALC><D_5463>A</D_5463></S_ALC>"
        "    <G_SG41><S_PCD><C_C501><D_5245>1</D_5245><D_5482>100</D_5482></C_C501></S_PCD></G_SG41>"
        "    <G_SG42><S_MOA><C_C516><D_5025>204</D_5025><D_5004>10</D_5004></C_C516></S_MOA></G_SG42>"
        "  </G_SG39>"
        "</G_SG26>"
    )
    pct, amt, gratis = _alc_pcd_moa_discount(sg26_gratis, Decimal("1"))
    assert pct == Decimal("100")
    assert amt == Decimal("10")
    assert gratis

    sg26_charge = _line(
        "<G_SG26>"
        "  <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "  <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>120</D_5118></C_C509></S_PRI>"
        "  <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>100</D_5118></C_C509></S_PRI>"
        "  <G_SG39>"
        "    <S_ALC><D_5463>A</D_5463></S_ALC>"
        "    <G_SG41><S_PCD><C_C501><D_5245>1</D_5245><D_5482>-20</D_5482></C_C501></S_PCD></G_SG41>"
        "    <G_SG42><S_MOA><C_C516><D_5025>204</D_5025><D_5004>-20</D_5004></C_C516></S_MOA></G_SG42>"
        "  </G_SG39>"
        "</G_SG26>"
    )
    pct, amt, gratis = _alc_pcd_moa_discount(sg26_charge, Decimal("1"))
    assert pct == Decimal("-20")
    assert amt == Decimal("-20")
    assert not gratis


def test_alc_ignores_charges() -> None:
    sg = _line(
        "<G_SG26>"
        "  <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "  <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>80</D_5118></C_C509></S_PRI>"
        "  <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>100</D_5118></C_C509></S_PRI>"
        "  <G_SG39>"
        "    <S_ALC><D_5463>C</D_5463></S_ALC>"
        "    <G_SG41><S_PCD><C_C501><D_5245>1</D_5245><D_5482>999</D_5482></C_C501></S_PCD></G_SG41>"
        "  </G_SG39>"
        "</G_SG26>"
    )
    pct, amt, gratis = _alc_pcd_moa_discount(sg, Decimal("1"))
    assert pct == Decimal("0")
    assert amt == Decimal("20")
    assert not gratis


def test_alc_quantizes_fallback_percent() -> None:
    sg = _line(
        "<G_SG26>"
        "  <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "  <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>80</D_5118></C_C509></S_PRI>"
        "  <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>99</D_5118></C_C509></S_PRI>"
        "</G_SG26>"
    )
    pct, amt, gratis = _alc_pcd_moa_discount(sg, Decimal("1"))
    assert pct == Decimal("19.19")
    assert amt == Decimal("19.00")
    assert not gratis
