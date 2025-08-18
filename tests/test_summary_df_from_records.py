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
    assert df.shape == (2, 6)
    assert df["WSM šifra"].tolist() == ["1", ""]
    assert df["WSM Naziv"].tolist() == ["", "B"]
    assert df["Količina"].tolist() == [2, 0]
    assert df["Znesek"].tolist() == [0, 0]
    assert df["Rabat (%)"].tolist() == [0, 0]
    assert df["Neto po rabatu"].tolist() == [0, 0]
