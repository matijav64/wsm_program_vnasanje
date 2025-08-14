from decimal import Decimal
from lxml import etree
import logging

from wsm.parsing.eslog import parse_invoice_totals


def test_parse_invoice_totals_detects_mismatch(caplog):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50><S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA></G_SG50>"
        "    <G_SG52><S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA></G_SG52>"
        "    <G_SG50><S_MOA><C_C516><D_5025>9</D_5025><D_5004>11.00</D_5004></C_C516></S_MOA></G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    tree = etree.fromstring(xml)
    with caplog.at_level(logging.WARNING):
        totals = parse_invoice_totals(tree)
    assert totals["net"] == Decimal("10")
    assert totals["vat"] == Decimal("2.20")
    assert totals["gross"] == Decimal("12.20")
    assert totals["mismatch"]
    assert any("Invoice total mismatch" in r.message for r in caplog.records)
