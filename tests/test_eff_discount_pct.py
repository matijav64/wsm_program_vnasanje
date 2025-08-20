from decimal import Decimal
import pandas as pd

from wsm.ui.review.helpers import compute_eff_discount_pct


def test_discount_derived_from_amounts_and_threshold():
    df = pd.DataFrame(
        {
            "vrednost": [Decimal("18"), Decimal("0.1")],
            "rabata": [Decimal("2"), Decimal("19.9")],
        }
    )
    pct = df.apply(compute_eff_discount_pct, axis=1)
    expected = pd.Series([Decimal("10.00"), Decimal("100.00")])
    pd.testing.assert_series_equal(pct, expected)


def test_handles_zero_base():
    df = pd.DataFrame(
        {
            "vrednost": [Decimal("0"), Decimal("100")],
            "rabata": [Decimal("0"), Decimal("10")],
        }
    )
    pct = df.apply(compute_eff_discount_pct, axis=1)
    expected = pd.Series([Decimal("0.00"), Decimal("9.09")])
    pd.testing.assert_series_equal(pct, expected)


def test_missing_columns_yield_zero():
    df = pd.DataFrame({"wsm_sifra": [1]})
    pct = df.apply(compute_eff_discount_pct, axis=1)
    expected = pd.Series([Decimal("0.00")])
    pd.testing.assert_series_equal(pct, expected)
