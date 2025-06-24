from __future__ import annotations
from pathlib import Path
from decimal import Decimal
import pandas as pd

from wsm.parsing.eslog import parse_eslog_invoice
from wsm.ui.review_links import _norm_unit
from wsm.supplier_store import load_suppliers as _load_supplier_map
from wsm.parsing.eslog import extract_header_net
from wsm.parsing.money import detect_round_step, round_to_step


def analyze_invoice(xml_path: str, suppliers_file: str | None = None) -> tuple[pd.DataFrame, Decimal, bool]:
    """Parse, normalize and group an eSLOG invoice.

    Lines with the same product code (``sifra_artikla``) and equal discount
    percentage (``rabata_pct``) are merged together. The product name is kept
    from the first occurrence.
    """
    sup_map = _load_supplier_map(Path(suppliers_file)) if suppliers_file else {}
    df = parse_eslog_invoice(xml_path, sup_map)

    # normalize units
    df[['kolicina', 'enota']] = [
        _norm_unit(row['kolicina'], row['enota'], row['naziv'], row['ddv_stopnja'])
        for _, row in df.iterrows()
    ]

    # group by product code and discount
    doc_mask = df['sifra_dobavitelja'] == '_DOC_'
    df_main = df[~doc_mask].copy()
    df_doc = df[doc_mask].copy()

    grouped = (
        df_main
        .groupby(['sifra_artikla', 'rabata_pct'], dropna=False, as_index=False)
        .agg({
            'naziv': 'first',
            'sifra_dobavitelja': 'first',
            'kolicina': 'sum',
            'enota': 'first',
            'vrednost': 'sum',
            'rabata': 'sum',
        })
    )
    grouped['cena_netto'] = grouped.apply(
        lambda r: r['vrednost'] / r['kolicina'] if r['kolicina'] else Decimal('0'),
        axis=1,
    )
    grouped['cena_bruto'] = grouped.apply(
        lambda r: (r['vrednost'] + r['rabata']) / r['kolicina'] if r['kolicina'] else Decimal('0'),
        axis=1,
    )

    result = pd.concat([grouped, df_doc], ignore_index=True)

    header_total = extract_header_net(Path(xml_path))
    raw_sum = Decimal(str(result['vrednost'].sum()))
    step = detect_round_step(header_total, raw_sum)
    line_sum = round_to_step(raw_sum, step)
    ok = abs(line_sum - header_total) <= step
    return result, header_total, ok
