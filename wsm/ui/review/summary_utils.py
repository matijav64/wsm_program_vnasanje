from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

import numpy as np
import pandas as pd

from .helpers import _safe_set_block
from .summary_columns import SUMMARY_COLS


def summary_df_from_records(records: Sequence[dict] | None) -> pd.DataFrame:
    """Create summary DataFrame from ``records``.

    Parameters
    ----------
    records:
        Sequence of mapping objects with column data. Missing keys or
        values are filled with defaults and the DataFrame is reindexed to
        :data:`SUMMARY_COLS`.
    """
    df = pd.DataFrame.from_records(records or [])
    df = df.reindex(columns=SUMMARY_COLS)

    numeric_cols = ["Količina", "Znesek", "Rabat (%)", "Neto po rabatu"]
    for col in numeric_cols:
        df[col] = df[col].apply(
            lambda x: (
                x
                if isinstance(x, Decimal)
                else Decimal(str(x)) if not pd.isna(x) and x != "" else Decimal("0")
            )
        )
    text_cols = ["WSM šifra", "WSM Naziv"]
    df = _safe_set_block(df, text_cols, df[text_cols].fillna(""))
    return df


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
