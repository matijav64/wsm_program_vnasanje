from __future__ import annotations

from decimal import Decimal
from typing import Sequence

import pandas as pd

from .helpers import _safe_set_block
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

    numeric_cols = [
        "Količina",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    ]

    def _to_decimal(x):
        if isinstance(x, Decimal):
            return x
        return Decimal(str(x)) if not pd.isna(x) and x != "" else Decimal("0")

    for col in numeric_cols:
        df[col] = df[col].map(_to_decimal)
    text_cols = ["WSM šifra", "WSM Naziv"]
    df = _safe_set_block(df, text_cols, df[text_cols].fillna(""))
    return df
