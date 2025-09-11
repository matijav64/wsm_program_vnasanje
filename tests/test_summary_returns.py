from decimal import Decimal

import pandas as pd

from wsm.ui.review.summary_utils import aggregate_summary_per_code


def test_aggregate_summary_handles_returns_and_ostalo():
    df = pd.DataFrame(
        {
            "WSM šifra": ["100100", "100100", "0"],
            "WSM Naziv": ["Artikel", "Artikel", ""],
            "Količina": [Decimal("20"), Decimal("-20"), Decimal("5")],
            "Vrnjeno": [Decimal("0"), Decimal("20"), Decimal("0")],
            "Znesek": [Decimal("200"), Decimal("-200"), Decimal("50")],
            "Rabat (%)": [Decimal("0.00")] * 3,
            "Neto po rabatu": [Decimal("200"), Decimal("-200"), Decimal("50")],
        }
    )

    out = aggregate_summary_per_code(df)
    row = out[out["WSM šifra"] == "100100"].iloc[0]
    assert row["Količina"] == Decimal("0")
    assert row["Vrnjeno"] == Decimal("20")

    last = out.iloc[-1]
    assert last["WSM šifra"] == ""
    assert last["WSM Naziv"] == "Ostalo"
