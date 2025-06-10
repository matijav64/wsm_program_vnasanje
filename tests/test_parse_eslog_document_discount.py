from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import parse_eslog_invoice


def _compute_doc_discount(xml_path: Path) -> Decimal:
    """Compute document discount sum the same way as parse_eslog_invoice."""
    NS = {"e": "urn:eslog:2.00"}
    root = ET.parse(xml_path).getroot()
    discounts = {"204": Decimal("0"), "260": Decimal("0")}

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

    doc_discount = discounts["204"] if discounts["204"] != 0 else discounts["260"]
    return doc_discount.quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_parse_eslog_invoice_returns_doc_discount_row():
    xml_path = Path("tests/PR5697-Slika2.XML")
    expected_discount = _compute_doc_discount(xml_path)
    df = parse_eslog_invoice(xml_path, {})
    doc_row = df[df["sifra_dobavitelja"] == "_DOC_"].iloc[0]

    assert doc_row["vrednost"] == -expected_discount
    assert doc_row["rabata_pct"] == Decimal("100.00")

