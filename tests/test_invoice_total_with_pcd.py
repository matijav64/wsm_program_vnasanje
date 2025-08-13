# flake8: noqa
"""Integration test ensuring gross total matches header with PCD."""

from decimal import Decimal
from wsm.parsing.eslog import parse_eslog_invoice


def test_invoice_total_with_pcd(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <S_PCD><C_C501><D_5482>10</D_5482></C_C501></S_PCD>"
        "      </G_SG39>"
        "      <G_SG34>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>19.80</D_5004></C_C516></S_MOA>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_PCD><C_C501><D_5482>10</D_5482></C_C501></S_PCD>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>98.82</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>17.82</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "inv.xml"
    path.write_text(xml)
    df, ok = parse_eslog_invoice(path)
    assert ok
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]
    assert doc_row["vrednost"] == Decimal("-9.00")
