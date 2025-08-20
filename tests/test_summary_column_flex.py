import pandas as pd
from decimal import Decimal
from wsm.ui.review.helpers import compute_eff_discount_pct_robust, first_existing_series

def test_eff_pct_from_alternatives():
    df = pd.DataFrame({
        "WSM šifra": ["200607","200607"],
        "Količina":  [6,6],
        "Net. po rab.": [Decimal("0"), Decimal("4.84")],
        "Skupna neto":  [Decimal("0"), Decimal("29.04")],
        "Bruto":        [Decimal("29.04"), Decimal("29.04")],
        "rabat":        [Decimal("29.04"), Decimal("0.00")],
    })
    pct = compute_eff_discount_pct_robust(df)
    assert pct.iloc[0] == Decimal("100.00")
    assert pct.iloc[1] == Decimal("0.00")
