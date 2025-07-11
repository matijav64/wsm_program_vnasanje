from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice


def _calc_unlinked_total(xml_path: Path) -> Decimal:
    df, ok = parse_eslog_invoice(xml_path, {})
    df = df[df["sifra_dobavitelja"] != "_DOC_"].copy()
    df["total_net"] = df["vrednost"]



    # all lines linked
    df["wsm_sifra"] = "X"
    unlinked_total = df[df["wsm_sifra"].isna()]["total_net"].sum()
    assert ok
    return unlinked_total


def test_unlinked_total_zero_when_all_lines_linked():
    xml = Path("tests/PR5707-Slika2.XML")
    assert _calc_unlinked_total(xml) == Decimal("0")
