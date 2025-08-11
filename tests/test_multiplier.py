from decimal import Decimal
import pandas as pd

from wsm.ui.review.gui import _apply_multiplier


def test_apply_multiplier_preserves_total_net():
    df = pd.DataFrame([
        {
            "kolicina_norm": Decimal("2"),
            "cena_po_rabatu": Decimal("50"),
            "cena_pred_rabatom": Decimal("60"),
            "total_net": Decimal("100"),
        }
    ])

    _apply_multiplier(df, 0, Decimal("5"))

    assert df.at[0, "kolicina_norm"] == Decimal("10")
    assert df.at[0, "cena_po_rabatu"] == Decimal("10")
    assert df.at[0, "cena_pred_rabatom"] == Decimal("12")
    assert df.at[0, "total_net"] == Decimal("100")
