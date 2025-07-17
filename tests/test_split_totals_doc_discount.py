from decimal import Decimal
from pathlib import Path
import pandas as pd

from wsm.parsing.eslog import parse_eslog_invoice, extract_header_net
from wsm.ui.review.helpers import _split_totals


def _prepare(path: Path):
    df, ok = parse_eslog_invoice(path)
    assert ok
    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    disc = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"].copy()
    df["total_net"] = df["vrednost"]
    df["is_gratis"] = False
    df["wsm_sifra"] = pd.NA
    return df, disc, extract_header_net(path)


def test_split_totals_linked_discount():
    path = Path("tests/minimal_doc_discount.xml")
    df, disc, header = _prepare(path)
    df.loc[0, "wsm_sifra"] = "X"
    linked, unlinked, total = _split_totals(df, disc)
    assert total == header
    assert linked == df.loc[0, "total_net"] + disc
    assert unlinked == Decimal("0")


def test_split_totals_unlinked_discount():
    path = Path("tests/minimal_doc_discount.xml")
    df, disc, header = _prepare(path)
    linked, unlinked, total = _split_totals(df, disc)
    assert total == header
    assert linked == Decimal("0")
    assert unlinked == df["total_net"].sum() + disc
