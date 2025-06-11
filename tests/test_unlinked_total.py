from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice, extract_header_net


def _calc_unlinked_total(xml_path: Path) -> Decimal:
    df = parse_eslog_invoice(xml_path, {})
    invoice_total = extract_header_net(xml_path)
    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"].copy()
    doc_discount_total = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"].copy()
    df["total_net"] = df["vrednost"]

    calculated_total = df["total_net"].sum() + doc_discount_total
    diff = invoice_total - calculated_total
    if abs(diff) <= Decimal("0.02") and diff != 0:
        if not df_doc.empty:
            doc_discount_total += diff
            df_doc.loc[df_doc.index, "vrednost"] += diff
            df_doc.loc[df_doc.index, "cena_bruto"] += abs(diff)
            df_doc.loc[df_doc.index, "rabata"] += abs(diff)
        else:
            # difference ignored
            pass

    # all lines linked
    df["wsm_sifra"] = "X"
    unlinked_total = df[df["wsm_sifra"].isna()]["total_net"].sum() + doc_discount_total
    return unlinked_total


def test_unlinked_total_zero_when_all_lines_linked():
    xml = Path("tests/PR5707-Slika2.XML")
    assert _calc_unlinked_total(xml) == Decimal("0")
