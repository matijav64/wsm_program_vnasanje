from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from lxml import etree as LET

# flake8: noqa

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    DEFAULT_DOC_DISCOUNT_CODES,
    extract_header_net,
    sum_moa,
)


def _compute_doc_discount(xml_path: Path) -> Decimal:
    """Compute document discount sum using ``sum_moa``."""
    root = LET.parse(xml_path).getroot()
    codes = list(DEFAULT_DOC_DISCOUNT_CODES)
    if "204" not in codes:
        codes.append("204")
    return sum_moa(root, codes)


def test_parse_eslog_invoice_returns_doc_discount_row():
    xml_path = Path("tests/minimal_doc_discount.xml")
    expected_discount = _compute_doc_discount(xml_path)
    df, ok = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == expected_discount
    assert doc_row["rabata_pct"] == Decimal("100.00")
    assert ok


def test_parse_eslog_invoice_handles_additional_discount_codes(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>131</D_5025><D_5004>-2.50</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc131.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-2.50")
    assert doc_row["rabata_pct"] == Decimal("100.00")
    assert ok


def test_parse_eslog_invoice_sums_multiple_discount_codes(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1.50</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>260</D_5025><D_5004>-2.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc_multi.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-3.50")
    assert doc_row["rabata_pct"] == Decimal("100.00")
    assert ok


def test_line_and_doc_discount_total_matches_header():
    xml_path = Path("tests/minimal_doc_discount.xml")
    df, ok = parse_eslog_invoice(xml_path)

    doc_rows = df[df["sifra_dobavitelja"] == "_DOC_"]
    assert not doc_rows.empty
    doc_value = doc_rows.iloc[0]["vrednost"]

    line_total = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    header_total = extract_header_net(xml_path)

    assert (line_total + doc_value).quantize(Decimal("0.01")) == header_total


def test_parse_eslog_invoice_handles_moa_176(tmp_path):
    """Invoices with document discount code 176 should yield a _DOC_ row."""
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>176</D_5025><D_5004>-5.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc176.xml"
    xml_path.write_text(xml)
    expected_discount = _compute_doc_discount(xml_path)

    df, _ = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == expected_discount
    assert doc_row["rabata_pct"] == Decimal("100.00")


def test_parse_eslog_invoice_handles_moa_500(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>500</D_5025><D_5004>-0.05</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc500.xml"
    xml_path.write_text(xml)

    df, _ = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-0.05")
    assert doc_row["rabata_pct"] == Decimal("100.00")


def test_parse_eslog_invoice_sums_duplicate_values(tmp_path):
    """Discounts with the same code and amount should all be summed."""
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc_dupes.xml"
    xml_path.write_text(xml)

    df, _ = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-2.00")
    assert doc_row["rabata_pct"] == Decimal("100.00")


def test_parse_eslog_invoice_handles_sg16_level_discount(tmp_path):
    xml_path = Path("tests/sg16_doc_discount.xml")
    expected = _compute_doc_discount(xml_path)
    df, _ = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == expected
    assert doc_row["rabata_pct"] == Decimal("100.00")


def test_parse_eslog_invoice_handles_sg20_level_discount(tmp_path):
    xml_path = Path("tests/sg20_doc_discount.xml")
    expected = _compute_doc_discount(xml_path)
    df, _ = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == expected
    assert doc_row["rabata_pct"] == Decimal("100.00")


def test_header_net_matches_lines_with_moa204():
    xml_path = Path("tests/header_net_204.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert not ok
    line_total = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    doc_value = df[df["sifra_dobavitelja"] == "_DOC_"]["vrednost"].sum()
    header_total = extract_header_net(xml_path)
    assert (line_total + doc_value).quantize(Decimal("0.01")) == header_total


def test_parse_eslog_invoice_ignores_vat_totals(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>38</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <G_SG52>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "        <S_MOA><C_C516><D_5025>131</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "      </G_SG52>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "vat_overlap.xml"
    xml_path.write_text(xml)
    df, ok = parse_eslog_invoice(xml_path)
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty
    assert ok
