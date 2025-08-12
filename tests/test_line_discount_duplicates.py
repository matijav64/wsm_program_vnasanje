from decimal import Decimal
import logging

from wsm.parsing.eslog import parse_eslog_invoice, extract_header_net


def test_line_discount_duplicates(tmp_path, caplog):
    xml = """
<Invoice xmlns="urn:eslog:2.00">
  <M_INVOIC>
    <G_SG26>
      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>
      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>
      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>
    </G_SG26>
    <G_SG26>
      <S_QTY><C_C186><D_6060>0</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>99</D_7140></C_C212></S_LIN>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>-2</D_5004></C_C516></S_MOA>
      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-2</D_5004></C_C516></S_MOA>
    </G_SG26>
    <G_SG50>
      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>8</D_5004></C_C516></S_MOA>
    </G_SG50>
    <G_SG50>
      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>8</D_5004></C_C516></S_MOA>
    </G_SG50>
  </M_INVOIC>
</Invoice>
"""
    path = tmp_path / "dup.xml"
    path.write_text(xml, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        df, ok = parse_eslog_invoice(path)
    assert ok
    assert "Invoice total mismatch" not in caplog.text
    assert "Line net mismatch" not in caplog.text

    # one real line plus document discount row
    assert len(df) == 2
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]
    assert doc_row["vrednost"] == Decimal("-2")
    assert extract_header_net(path) == Decimal("8")
