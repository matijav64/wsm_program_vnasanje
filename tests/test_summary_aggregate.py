import pandas as pd
from decimal import Decimal
from wsm.ui.review.summary_utils import aggregate_summary_per_code

def test_aggregate_summary_per_code_dedups_by_code():
    df = pd.DataFrame(
        {
            "WSM šifra": ["100100", "100100", "100031", ""],
            "WSM Naziv": ["PIVO SOD 50/1", "PIVO SOD 50/1", "CEDEVITA VR.", ""],
            "Količina": [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
            "Znesek": [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")],
            "Rabat (%)": [Decimal("5"), Decimal("0"), Decimal("0"), Decimal("0")],
            "Neto po rabatu": [Decimal("9"), Decimal("18"), Decimal("30"), Decimal("40")],
        }
    )
    out = aggregate_summary_per_code(df)
    assert list(out["WSM šifra"]) == ["100031", "100100", ""]
    r100100 = out[out["WSM šifra"] == "100100"].iloc[0]
    assert r100100["Znesek"] == Decimal("30")
    assert r100100["Neto po rabatu"] == Decimal("27")
    assert out.iloc[-1]["WSM Naziv"].lower() == "ostalo"


def test_aggregate_summary_per_code_treats_named_ostalo_as_uncoded():
    df = pd.DataFrame(
        {
            "WSM šifra": ["100100", "ABC", ""],
            "WSM Naziv": ["PIVO", "Ostalo", ""],
            "Količina": [Decimal("1"), Decimal("2"), Decimal("3")],
            "Znesek": [Decimal("10"), Decimal("20"), Decimal("30")],
            "Rabat (%)": [Decimal("0"), Decimal("0"), Decimal("0")],
            "Neto po rabatu": [Decimal("10"), Decimal("20"), Decimal("30")],
        }
    )
    out = aggregate_summary_per_code(df)
    assert list(out["WSM šifra"]) == ["100100", ""]
    # "Ostalo" row combines amounts from named 'Ostalo' and empty-code rows
    assert out.iloc[-1]["WSM Naziv"].lower() == "ostalo"
    assert out.iloc[-1]["Količina"] == Decimal("5")

