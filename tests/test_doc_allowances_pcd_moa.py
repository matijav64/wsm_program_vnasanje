# flake8: noqa
"""Tests for document-level PCD and MOA allowances."""

from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import _apply_doc_allowances_sequential


def test_doc_level_allowances_sequential():
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_PCD><C_C501><D_5482>10</D_5482></C_C501></S_PCD>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>260</D_5025><D_5004>-5</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = LET.fromstring(xml)
    net, allow, charge = _apply_doc_allowances_sequential(Decimal("100"), root)
    assert net == Decimal("85.00")
    assert allow == Decimal("15.00")
    assert charge == Decimal("0.00")
