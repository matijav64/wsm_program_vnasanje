from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice, DEFAULT_DOC_DISCOUNT_CODES


def _compute_doc_discount(xml_path: Path) -> Decimal:
    """Compute document discount sum the same way as parse_eslog_invoice."""
    NS = {"e": "urn:eslog:2.00"}
    root = ET.parse(xml_path).getroot()
    discounts = {code: Decimal("0") for code in DEFAULT_DOC_DISCOUNT_CODES}

    for seg in root.findall(".//e:G_SG50", NS) + root.findall(".//e:G_SG20", NS):
        for moa in seg.findall(".//e:S_MOA", NS):
            code_el = moa.find("./e:C_C516/e:D_5025", NS)
            if code_el is None:
                continue
            code = code_el.text or ""
            if code in discounts:
                val_el = moa.find("./e:C_C516/e:D_5004", NS)
                amt = Decimal((val_el.text or "0").replace(",", "."))
                discounts[code] += amt.quantize(Decimal("0.01"), ROUND_HALF_UP)

    doc_discount = Decimal("0")
    for code in DEFAULT_DOC_DISCOUNT_CODES:
        if discounts.get(code):
            doc_discount = discounts[code]
            break
    return doc_discount.quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_parse_eslog_invoice_returns_doc_discount_row():
    xml_path = Path("tests/PR5697-Slika2.XML")
    expected_discount = _compute_doc_discount(xml_path)
    df = parse_eslog_invoice(xml_path, {})
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == -expected_discount
    assert doc_row["rabata_pct"] == Decimal("100.00")


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

    df = parse_eslog_invoice(xml_path, {})
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == Decimal("-2.50")
    assert doc_row["rabata_pct"] == Decimal("100.00")

