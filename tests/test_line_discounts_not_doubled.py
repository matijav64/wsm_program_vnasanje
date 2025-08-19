from decimal import Decimal
from wsm.parsing import eslog
from lxml import etree as LET


def test_line_discount_pcd_and_moa204_single() -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>2</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0003</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item C</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <G_SG41>"
        "          <S_PCD><C_C501><D_5249>1</D_5249><D_5482>10</D_5482></C_C501></S_PCD>"
        "        </G_SG41>"
        "      </G_SG39>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>18</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = LET.fromstring(xml)
    eslog._force_ns_for_doc(root)
    sg26 = root.find(".//e:G_SG26", eslog.NS)
    disc_direct, disc_moa, pct_disc = eslog._line_discount_components(sg26)

    assert disc_direct == Decimal("2")
    assert disc_moa == Decimal("0")
    assert pct_disc == Decimal("0")
