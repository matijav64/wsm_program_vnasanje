from decimal import Decimal
from lxml import etree as LET

from wsm.parsing import eslog


NS = {"e": "urn:eslog:2.00"}


def _seg(xml: str) -> LET._Element:
    return LET.fromstring(xml)


def test_line_with_100pct_discount_is_doc_discount():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
          <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) == Decimal("2.00")
    assert eslog._line_net(seg) == Decimal("0.00")


def test_line_with_100pct_discount_from_sg39_allowance():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
          <G_SG39>
            <S_ALC><D_5463>A</D_5463></S_ALC>
            <G_SG42>
              <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>
            </G_SG42>
          </G_SG39>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) == Decimal("2.00")
    assert eslog._line_net(seg) == Decimal("0.00")


def test_multiple_sg39_allowances_are_summed():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
          <G_SG39>
            <S_ALC><D_5463>A</D_5463></S_ALC>
            <G_SG42>
              <S_MOA><C_C516><D_5025>204</D_5025><D_5004>1</D_5004></C_C516></S_MOA>
            </G_SG42>
          </G_SG39>
          <G_SG39>
            <S_ALC><D_5463>A</D_5463></S_ALC>
            <G_SG42>
              <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>
            </G_SG42>
          </G_SG39>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) == Decimal("3.00")


def test_moa260_discount():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
          <S_MOA><C_C516><D_5025>260</D_5025><D_5004>5</D_5004></C_C516></S_MOA>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) == Decimal("5.00")


def test_charge_is_ignored():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
          <G_SG39>
            <S_ALC><D_5463>C</D_5463></S_ALC>
            <G_SG42>
              <S_MOA><C_C516><D_5025>204</D_5025><D_5004>5</D_5004></C_C516></S_MOA>
            </G_SG42>
          </G_SG39>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) is None
    assert eslog._line_net(seg) == Decimal("0.00")


def test_zero_price_without_allowance_is_regular_zero_line():
    seg = _seg(
        """
        <G_SG26 xmlns='urn:eslog:2.00'>
          <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
          <S_LIN><C_C212><D_7140>Z</D_7140></C_C212></S_LIN>
          <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>
          <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>
        </G_SG26>
        """
    )
    assert eslog._doc_discount_from_line(seg) is None
    assert eslog._line_net(seg) == Decimal("0.00")
