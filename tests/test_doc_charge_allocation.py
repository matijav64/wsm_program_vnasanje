from decimal import Decimal
from pathlib import Path

from lxml import etree as LET

from wsm.parsing.eslog import (
    build_invoice_model,
    parse_eslog_invoice,
    parse_invoice_totals,
)


def test_document_charge_is_informational_only(tmp_path: Path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10.00</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>C</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>504</D_5025><D_5004>2.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>12.00</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.64</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>14.64</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )

    xml_path = tmp_path / "doc_charge.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    assert ok

    main_lines = df[~df["sifra_dobavitelja"].isin({"_DOC_", "DOC_CHG"})]
    doc_rows = df[df["sifra_dobavitelja"] == "DOC_CHG"]

    assert not doc_rows.empty
    assert main_lines["vrednost"].sum().quantize(Decimal("0.01")) == Decimal("12.00")
    assert main_lines["ddv"].sum().quantize(Decimal("0.01")) == Decimal("2.64")

    meta = parse_invoice_totals(LET.parse(xml_path))
    assert meta["net"] == Decimal("12.00")
    assert meta["vat"] == Decimal("2.64")
    assert meta["gross"] == Decimal("14.64")

    model = build_invoice_model(LET.parse(xml_path))
    assert model.net_total == Decimal("12.00")
    assert model.vat_total == Decimal("2.64")
    assert model.gross_total == Decimal("14.64")

