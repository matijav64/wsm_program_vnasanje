import math
from decimal import Decimal

import pandas as pd

from wsm.ui.review.summary_utils import vectorized_discount_pct


def test_vectorized_discount_pct_zero_and_nan_base():
    base = pd.Series([0, math.nan, 100])
    after = pd.Series([10, 5, 75])
    result = vectorized_discount_pct(base, after)
    assert result.tolist() == [
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("25.00"),
    ]
