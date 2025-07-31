from decimal import Decimal
import pandas as pd
from wsm.ui.review.helpers import _split_totals


def test_split_totals_vat_calculation():
    df = pd.DataFrame({"wsm_sifra": ["X"], "total_net": [Decimal("100")]})
    net, vat, gross = _split_totals(
        df, Decimal("0"), vat_rate=Decimal("0.095")
    )
    assert (net, vat, gross) == (
        Decimal("100"),
        Decimal("9.5"),
        Decimal("109.5"),
    )
