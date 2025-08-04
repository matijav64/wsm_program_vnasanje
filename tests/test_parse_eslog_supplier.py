from pathlib import Path
from decimal import Decimal
from wsm.parsing import eslog


def test_parse_eslog_invoice_uses_ahp_when_no_va():
    xml = Path(__file__).with_suffix("").with_name("vat_ahp_only.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
    assert not df.empty
    assert set(df["sifra_dobavitelja"]) == {"SI76543210"}
    assert ok


def test_parse_eslog_invoice_sets_supplier_when_missing(monkeypatch, tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG2>"
        "      <S_NAD>"
        "        <D_3035>SU</D_3035>"
        "        <C_C082><D_3039>SUP</D_3039></C_C082>"
        "        <C_C080><D_3036>Test</D_3036></C_C080>"
        "      </S_NAD>"
        "    </G_SG2>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060>"
        "<D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125>"
        "<D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025>"
        "<D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "invoice.xml"
    xml_path.write_text(xml)

    orig_df = eslog.pd.DataFrame

    def fake_df(*args, **kwargs):
        df = orig_df(*args, **kwargs)
        if "sifra_dobavitelja" in df.columns:
            df["sifra_dobavitelja"] = ""
        return df

    monkeypatch.setattr(eslog.pd, "DataFrame", fake_df)
    df, ok = eslog.parse_eslog_invoice(xml_path)
    assert set(df["sifra_dobavitelja"]) == {"SUP"}
    assert ok


def test_line_discount_is_applied():
    xml = Path("tests/discount_line.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    assert ok
    line = df.iloc[0]
    assert line["rabata"] == Decimal("2.00")
    assert line["vrednost"] == Decimal("18.00")
    assert line["cena_bruto"] == Decimal("10")
    assert line["cena_netto"] == Decimal("9.0000")


def test_line_discount_factor():
    xml = Path("tests/discount_line_factor.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    assert ok
    line = df.iloc[0]
    assert line["rabata"] == Decimal("2.00")
    assert line["vrednost"] == Decimal("18.00")
    assert line["cena_bruto"] == Decimal("10")
    assert line["cena_netto"] == Decimal("9.0000")


def test_line_discount_amount():
    xml = Path("tests/discount_line_amount.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    assert ok
    line = df.iloc[0]
    assert line["rabata"] == Decimal("2.00")
    assert line["vrednost"] == Decimal("18.00")
    assert line["cena_bruto"] == Decimal("10")
    assert line["cena_netto"] == Decimal("9.0000")


def test_line_discount_moa_and_pcd_are_summed():
    xml = Path("tests/discount_line_both.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    assert ok
    line = df.iloc[0]
    assert line["rabata"] == Decimal("3.00")
    assert line["vrednost"] == Decimal("17.00")
    assert line["cena_bruto"] == Decimal("10")
    assert line["cena_netto"] == Decimal("8.5000")


def test_line_discount_without_namespace():
    xml = Path("tests/discount_line_no_ns.xml")
    df, ok = eslog.parse_eslog_invoice(xml)
    assert ok
    line = df.iloc[0]
    assert line["rabata"] == Decimal("2.00")
    assert line["vrednost"] == Decimal("18.00")
    assert line["cena_bruto"] == Decimal("10")
    assert line["cena_netto"] == Decimal("9.0000")
