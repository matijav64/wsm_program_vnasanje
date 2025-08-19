from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice


def test_doc_discount_no_vat_mismatch(tmp_path: Path) -> None:
    xml = """
<Invoice xmlns='urn:eslog:2.00'>
  <M_INVOIC>
    <G_SG26>
      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>
      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>
      <G_SG34>
        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>
        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>
      </G_SG34>
    </G_SG26>
    <G_SG50><S_MOA><C_C516><D_5025>389</D_5025><D_5004>9</D_5004></C_C516></S_MOA></G_SG50>
    <G_SG50>
      <S_ALC><D_5463>A</D_5463></S_ALC>
      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1</D_5004></C_C516></S_MOA>
    </G_SG50>
    <G_SG50><S_MOA><C_C516><D_5025>9</D_5025><D_5004>11.20</D_5004></C_C516></S_MOA></G_SG50>
  </M_INVOIC>
</Invoice>
"""
    path = tmp_path / "doc_discount.xml"
    path.write_text(xml, encoding="utf-8")

    df, ok = parse_eslog_invoice(path)

    assert ok
    assert not df.attrs.get("vat_mismatch")
    line = df[df["sifra_dobavitelja"] != "_DOC_"].iloc[0]
    assert line["ddv"] == Decimal("2.20")


def test_doc_discount_vat_totals_preserved(tmp_path: Path) -> None:
    xml = """
<Invoice xmlns='urn:eslog:2.00'>
  <M_INVOIC>
    <G_SG26>
      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>
      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>
      <G_SG34>
        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>
        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>
      </G_SG34>
    </G_SG26>
    <G_SG26>
      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>
      <S_LIN><C_C212><D_7140>2</D_7140></C_C212></S_LIN>
      <S_IMD><C_C273><D_7008>Item2</D_7008></C_C273></S_IMD>
      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>5</D_5004></C_C516></S_MOA>
      <G_SG34>
        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>
        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>1.10</D_5004></C_C516></S_MOA>
      </G_SG34>
    </G_SG26>
    <G_SG50><S_MOA><C_C516><D_5025>389</D_5025><D_5004>14</D_5004></C_C516></S_MOA></G_SG50>
    <G_SG50>
      <S_ALC><D_5463>A</D_5463></S_ALC>
      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1</D_5004></C_C516></S_MOA>
    </G_SG50>
    <G_SG50><S_MOA><C_C516><D_5025>9</D_5025><D_5004>17.30</D_5004></C_C516></S_MOA></G_SG50>
  </M_INVOIC>
</Invoice>
"""
    path = tmp_path / "doc_discount_multi.xml"
    path.write_text(xml, encoding="utf-8")

    df, ok = parse_eslog_invoice(path)

    assert ok
    assert not df.attrs.get("vat_mismatch")
    vat_total = df[df["sifra_dobavitelja"] != "_DOC_"]["ddv"].sum()
    assert vat_total == Decimal("3.30")
