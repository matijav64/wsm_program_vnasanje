from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    DEFAULT_DOC_DISCOUNT_CODES,
    extract_header_net,
)


def _compute_doc_discount(xml_path: Path) -> Decimal:
    """Compute document discount sum the same way as parse_eslog_invoice."""
    NS = {"e": "urn:eslog:2.00"}
    root = ET.parse(xml_path).getroot()
    discounts = {code: Decimal("0") for code in DEFAULT_DOC_DISCOUNT_CODES}
    seen_segments = set()

    for seg in root.findall(".//e:G_SG50", NS) + root.findall(".//e:G_SG20", NS):
        for moa in seg.findall(".//e:S_MOA", NS):
            code_el = moa.find("./e:C_C516/e:D_5025", NS)
            if code_el is None:
                continue
            code = code_el.text or ""
            if code in discounts:
                val_el = moa.find("./e:C_C516/e:D_5004", NS)
                amt = Decimal((val_el.text or "0").replace(",", "."))
                amt = amt.quantize(Decimal("0.01"), ROUND_HALF_UP)
                key = (code, amt, id(moa))
                if key in seen_segments:
                    continue
                seen_segments.add(key)
                discounts[code] += amt

    # Sum all matching discount codes
    doc_discount = sum(
        (discounts.get(code) or Decimal("0")) for code in DEFAULT_DOC_DISCOUNT_CODES
    )
    return doc_discount.quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_parse_eslog_invoice_returns_doc_discount_row():
    xml_path = Path("tests/PR5697-Slika2.XML")
    expected_discount = _compute_doc_discount(xml_path)
    df, ok = parse_eslog_invoice(xml_path)
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]
    
    assert doc_row["vrednost"] == -expected_discount
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
        "      <S_MOA><C_C516><D_5025>131</D_5025><D_5004>2.50</D_5004></C_C516></S_MOA>"
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
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>1.50</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>260</D_5025><D_5004>2.00</D_5004></C_C516></S_MOA>"
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


def test_parse_eslog_invoice_handles_moa_176():
    """Invoices with document discount code 176 should yield a _DOC_ row."""
    xml_path = Path("tests/PR5690-Slika1.XML")
    expected_discount = _compute_doc_discount(xml_path)

    df, _ = parse_eslog_invoice(xml_path, {})
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == -expected_discount
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
        "      <S_MOA><C_C516><D_5025>500</D_5025><D_5004>0.05</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc500.xml"
    xml_path.write_text(xml)

    df, _ = parse_eslog_invoice(xml_path, {})
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
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>1.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>1.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "disc_dupes.xml"
    xml_path.write_text(xml)

    df, _ = parse_eslog_invoice(xml_path, {})
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-2.00")
    assert doc_row["rabata_pct"] == Decimal("100.00")
