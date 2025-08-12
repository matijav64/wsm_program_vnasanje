from decimal import Decimal
from pathlib import Path

import pytest

from wsm.parsing.eslog import parse_eslog_invoice


def test_line_taxamount_infers_rate(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    xml = """
<Invoice xmlns='urn:eslog:2.00'
         xmlns:cbc='urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'>
  <M_INVOIC>
    <G_SG26>
      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>
      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>
      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>
      <G_SG34>
        <cbc:TaxAmount>2.20</cbc:TaxAmount>
        <S_TAX><C_C243><D_5278>0</D_5278></C_C243></S_TAX>
      </G_SG34>
    </G_SG26>
    <G_SG50><S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA></G_SG50>
    <G_SG50><S_MOA><C_C516><D_5025>9</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA></G_SG50>
  </M_INVOIC>
</Invoice>
"""
    path = tmp_path / "taxamount.xml"
    path.write_text(xml, encoding="utf-8")

    with caplog.at_level("ERROR"):
        df, ok = parse_eslog_invoice(path)

    assert ok
    assert not df.attrs.get("vat_mismatch")
    assert not any("VAT mismatch" in rec.message for rec in caplog.records)
    assert df.iloc[0]["ddv_stopnja"] == Decimal("22")
