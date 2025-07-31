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


def test_split_totals_matches_header_without_doc_row():
    path = Path("tests/minimal_line_discount.xml")
    df, disc, header = _prepare(path)
    assert disc == Decimal("0")
    df.loc[0, "wsm_sifra"] = "X"
    net, vat, gross = _split_totals(df, disc, vat_rate=Decimal("0"))
    assert net == df["total_net"].sum()
    assert vat == Decimal("0")
    assert gross == header


def test_split_totals_matches_header_with_doc_row():
    path = Path("tests/minimal_doc_discount.xml")
    df, disc, header = _prepare(path)
    df.loc[0, "wsm_sifra"] = "X"
    net, vat, gross = _split_totals(df, disc, vat_rate=Decimal("0"))
    assert (df["total_net"].sum() + disc).quantize(Decimal("0.01")) == net
    assert vat == Decimal("0")
    assert gross == net
