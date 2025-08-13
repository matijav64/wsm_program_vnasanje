# flake8: noqa
"""Tests for sequential PCD and MOA handling on lines."""

from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import _line_net


def test_sequential_pcd_moa_on_line():
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <S_PCD><C_C501><D_5482>10</D_5482></C_C501></S_PCD>"
        "        <S_MOA><C_C516><D_5025>204</D_5025><D_5004>5</D_5004></C_C516></S_MOA>"
        "      </G_SG39>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = LET.fromstring(xml)
    sg26 = root.find(".//{urn:eslog:2.00}G_SG26")
    assert _line_net(sg26) == Decimal("85.00")
