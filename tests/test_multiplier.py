from decimal import Decimal

import pandas as pd

from wsm.ui.review.gui import _apply_multiplier


def test_apply_multiplier():
    df = pd.DataFrame(
        [
            {
                "kolicina_norm": Decimal("2"),
                "cena_pred_rabatom": Decimal("20"),
                "cena_po_rabatu": Decimal("10"),
                "total_net": Decimal("20"),
            }
        ]
    )

    original_total = df.at[0, "total_net"]
    _apply_multiplier(df, 0, Decimal("10"))

    assert df.at[0, "kolicina_norm"] == Decimal("20")
    assert df.at[0, "cena_pred_rabatom"] == Decimal("2")
    assert df.at[0, "cena_po_rabatu"] == Decimal("1")
    assert df.at[0, "total_net"] == original_total
