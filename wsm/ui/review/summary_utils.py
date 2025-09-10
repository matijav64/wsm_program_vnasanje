from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

import numpy as np
import pandas as pd

from .helpers import _safe_set_block, _norm_wsm_code
from .summary_columns import SUMMARY_HEADS, SUMMARY_COLS  # noqa: F401


def summary_df_from_records(records: Sequence[dict] | None) -> pd.DataFrame:
    """Create summary DataFrame from ``records``.

    Parameters
    ----------
    records:
        Sequence of mapping objects with column data. Missing keys or
        values are filled with defaults and the DataFrame is reindexed to
        :data:`SUMMARY_HEADS`.
    """
    df = pd.DataFrame.from_records(records or [], coerce_float=False)
    df = df.reindex(columns=SUMMARY_HEADS)

    numeric_cols = ["Količina", "Znesek", "Rabat (%)", "Neto po rabatu"]

    def _to_decimal(x):
        if isinstance(x, Decimal):
            return x
        return Decimal(str(x)) if not pd.isna(x) and x != "" else Decimal("0")

    for col in numeric_cols:
        df[col] = df[col].map(_to_decimal)
    text_cols = ["WSM šifra", "WSM Naziv"]
    df = _safe_set_block(df, text_cols, df[text_cols].fillna(""))
    return df


def _sum_decimal(series: pd.Series) -> Decimal:
    total = Decimal("0")
    for x in series:
        if isinstance(x, Decimal):
            total += x
        elif x is None:
            continue
        else:
            try:
                if pd.isna(x) or x == "":
                    continue
            except Exception:
                pass
            total += Decimal(str(x))
    return total


def _pick_name(series: pd.Series) -> str:
    vals = series.astype("string").fillna("").str.strip()
    vals = vals[vals != ""]
    if vals.empty:
        return ""
    modes = vals.mode(dropna=True)
    return modes.iloc[0] if len(modes) else vals.iloc[0]


def aggregate_summary_per_code(df_summary: pd.DataFrame) -> pd.DataFrame:
    """
    Združi povzetek na ENO vrstico na WSM šifro.
    Vrstice brez kode se združijo v enotno 'Ostalo'.
    """
    if df_summary.empty:
        return df_summary

    heads = ["WSM šifra", "WSM Naziv", "Količina", "Znesek", "Rabat (%)", "Neto po rabatu"]
    for h in heads:
        if h not in df_summary.columns:
            df_summary[h] = None

    df = df_summary[heads].copy()
    df["WSM šifra"] = df["WSM šifra"].map(_norm_wsm_code)

    # Tretiraj kot nekodirano tudi, če je naziv že "Ostalo"
    name_is_ostalo = (
        df["WSM Naziv"].astype("string").fillna("").str.strip().str.lower() == "ostalo"
    )
    coded_mask = df["WSM šifra"].astype(str).str.strip().ne("") & ~name_is_ostalo
    coded = df.loc[coded_mask].copy()
    uncoded = df.loc[~coded_mask].copy()

    parts = []
    if not coded.empty:
        agg = coded.groupby("WSM šifra", sort=True).agg({
            "WSM Naziv": _pick_name,
            "Količina": _sum_decimal,
            "Znesek": _sum_decimal,
            "Neto po rabatu": _sum_decimal,
        })
        agg["Rabat (%)"] = Decimal("0.00")
        agg = agg.reset_index()
        parts.append(agg)

    if not uncoded.empty:
        row = {
            "WSM šifra": "",
            "WSM Naziv": "Ostalo",
            "Količina": _sum_decimal(uncoded["Količina"]),
            "Znesek": _sum_decimal(uncoded["Znesek"]),
            "Rabat (%)": Decimal("0.00"),
            "Neto po rabatu": _sum_decimal(uncoded["Neto po rabatu"]),
        }
        parts.append(pd.DataFrame([row], columns=heads))

    out = pd.concat(parts, ignore_index=True) if parts else df.head(0)
    order = (out["WSM Naziv"].astype(str).str.lower() == "ostalo").astype(int)
    return (
        out.assign(_o=order)
           .sort_values(["_o", "WSM šifra"])
           .drop(columns="_o")
           .reset_index(drop=True)
    )

def vectorized_discount_pct(base, after) -> pd.Series:
    """Return discount percentage for ``base`` and ``after`` values.

    Both inputs are converted to numeric form and division by zero is
    handled gracefully. The result is expressed in percent with two
    decimal places as :class:`~decimal.Decimal` values.
    """
    base_num = pd.to_numeric(base, errors="coerce")
    after_num = pd.to_numeric(after, errors="coerce")
    base_arr = base_num.to_numpy(dtype=float)
    after_arr = after_num.to_numpy(dtype=float)
    pct_arr = np.zeros_like(base_arr, dtype=float)
    np.divide(base_arr - after_arr, base_arr, out=pct_arr, where=base_arr != 0)
    pct_arr *= 100
    pct_series = pd.Series(pct_arr, index=base_num.index if isinstance(base_num, pd.Series) else None)
    pct_series = pct_series.fillna(0)
    return pct_series.apply(lambda x: Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))
