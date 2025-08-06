from __future__ import annotations
from pathlib import Path
from decimal import Decimal
import logging
import pandas as pd

from wsm.parsing.eslog import parse_eslog_invoice
from wsm.ui.review.helpers import _norm_unit
from wsm.parsing.eslog import extract_header_net
from wsm.parsing.money import detect_round_step, round_to_step

log = logging.getLogger(__name__)


def analyze_invoice(
    xml_path: str,
    suppliers_file: str | None = None,
    *,
    raise_on_vat_mismatch: bool = False,
) -> tuple[pd.DataFrame, Decimal, bool]:
    """Parse, normalize and group an eSLOG invoice.

    Lines with the same product code (``sifra_artikla``) and equal discount
    percentage (``rabata_pct``) are merged together. The product name is kept
    from the first occurrence.

    Parameters
    ----------
    xml_path:
        Path to the invoice XML file.
    suppliers_file:
        Optional path to supplier definitions.
    raise_on_vat_mismatch:
        When ``True`` a :class:`ValueError` is raised if any line's VAT
        ``TaxAmount`` differs from the value calculated from net amount and
        rate.
    """
    df, grand_ok = parse_eslog_invoice(xml_path)
    vat_mismatch = df.attrs.get("vat_mismatch", False)
    if vat_mismatch:
        log.error("VAT mismatch detected in invoice %s", xml_path)
        if raise_on_vat_mismatch:
            raise ValueError("VAT amount differs from calculated value")

    # normalize units
    df[["kolicina", "enota"]] = [
        _norm_unit(
            row["kolicina"],
            row["enota"],
            row["naziv"],
            row["ddv_stopnja"],
            row.get("sifra_artikla"),
        )
        for _, row in df.iterrows()
    ]

    # group by product code and discount
    doc_mask = df["sifra_dobavitelja"] == "_DOC_"
    df_main = df[~doc_mask].copy()
    df_doc = df[doc_mask].copy()

    grouped = df_main.groupby(
        ["sifra_artikla", "rabata_pct"], dropna=False, as_index=False
    ).agg(
        {
            "naziv": "first",
            "sifra_dobavitelja": "first",
            "kolicina": "sum",
            "enota": "first",
            "vrednost": "sum",
            "rabata": "sum",
        }
    )
    grouped["cena_netto"] = grouped.apply(
        lambda r: (
            r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    grouped["cena_bruto"] = grouped.apply(
        lambda r: (
            (r["vrednost"] + r["rabata"]) / r["kolicina"]
            if r["kolicina"]
            else Decimal("0")
        ),
        axis=1,
    )

    result = pd.concat([grouped, df_doc], ignore_index=True)

    header_total = extract_header_net(Path(xml_path))
    raw_sum = Decimal(str(result["vrednost"].sum()))
    step = detect_round_step(header_total, raw_sum)
    line_sum = round_to_step(raw_sum, step)
    ok = abs(line_sum - header_total) <= step and grand_ok and not vat_mismatch
    return result, header_total, ok
