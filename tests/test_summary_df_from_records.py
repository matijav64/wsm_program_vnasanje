from decimal import Decimal

from wsm.ui.review.summary_utils import SUMMARY_COLS, summary_df_from_records


def test_summary_empty_returns_empty_df():
    df = summary_df_from_records([])
    assert df.empty
    assert list(df.columns) == SUMMARY_COLS


def test_summary_missing_fields_filled():
    records = [
        {"WSM šifra": "1", "Količina": 2},  # missing "WSM Naziv"
        {"WSM Naziv": "B"},  # missing "WSM šifra" and "Količina"
    ]
    df = summary_df_from_records(records)
    assert df.shape == (2, 7)
    assert df["WSM šifra"].tolist() == ["1", ""]
    assert df["WSM Naziv"].tolist() == ["", "B"]
    assert df["Količina"].tolist() == [2, 0]
    assert df["Vrnjeno"].tolist() == [0, 0]
    assert df["Znesek"].tolist() == [0, 0]
    assert df["Rabat (%)"].tolist() == [0, 0]
    assert df["Neto po rabatu"].tolist() == [0, 0]
    for col in [
        "Količina",
        "Vrnjeno",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    ]:
        assert all(isinstance(x, Decimal) for x in df[col])


def test_summary_from_records_shapes_and_types():
    rows = [
        {
            "WSM šifra": "100",
            "WSM Naziv": "Artikel",
            "Količina": "2",
            "Znesek": "10.5",
            "Rabat (%)": "5",
            "Neto po rabatu": "9.975",
        }
    ]
    df = summary_df_from_records(rows)
    assert list(df.columns)
    assert df.loc[0, "Količina"] == Decimal("2")
    assert df.loc[0, "Znesek"] == Decimal("10.5")
    assert df.loc[0, "Rabat (%)"] == Decimal("5")
