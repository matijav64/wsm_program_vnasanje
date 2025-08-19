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
    pct = compute_eff_discount_pct(df)
    assert list(pct) == [Decimal("10.00"), Decimal("100.00")]


def test_doc_discount_ignored_when_base_zero():
    df = pd.DataFrame(
        {
            "net_po_rab": [Decimal("0"), Decimal("100")],
            "rabata": [Decimal("0"), Decimal("10")],
        }
    )
    pct = compute_eff_discount_pct(df, doc_discount_pct=Decimal("10"))
    assert pct.tolist() == [Decimal("0.00"), Decimal("19.00")]


def test_doc_discount_ignored_when_no_amounts():
    df = pd.DataFrame({"wsm_sifra": [1]})
    pct = compute_eff_discount_pct(df, doc_discount_pct=Decimal("10"))
    assert pct.tolist() == [Decimal("0.00")]
