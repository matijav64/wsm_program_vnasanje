from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import _line_net, _line_pct_discount


def _seg(xml: str) -> LET._Element:
    return LET.fromstring(xml)


def test_pct_discount_uses_moa25_base():
    seg = _seg(
        """
        <G_SG26 xmlns="urn:eslog:2.00">
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>100</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>
          <G_SG39>
            <S_ALC><D_5463>A</D_5463><C_C552><D_5189>95</D_5189></C_C552></S_ALC>
            <S_MOA><C_C516><D_5025>25</D_5025><D_5004>50</D_5004></C_C516></S_MOA>
            <G_SG41>
              <S_PCD><C_C501><D_5249>1</D_5249><D_5482>10</D_5482></C_C501></S_PCD>
            </G_SG41>
          </G_SG39>
        </G_SG26>
        """
    )

    assert _line_pct_discount(seg) == Decimal("5.00")
    assert _line_net(seg) == Decimal("95.00")

