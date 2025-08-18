from itertools import zip_longest

from wsm.ui.review.summary_utils import SUMMARY_COLS, summary_df_from_records


def test_summary_empty_returns_empty_df():
    df = summary_df_from_records([])
    assert df.empty
    assert list(df.columns) == SUMMARY_COLS


def test_summary_handles_mismatched_lengths():
    sifre = ["1", "2", "3"]
    nazivi = ["A", "B"]
    kolicine = [1]
    records = [
        {"WSM šifra": s, "WSM Naziv": n, "Količina": k}
        for s, n, k in zip_longest(sifre, nazivi, kolicine)
    ]
    df = summary_df_from_records(records)
    assert df.shape == (3, 6)
    assert df["WSM šifra"].tolist() == ["1", "2", "3"]
    assert df["WSM Naziv"].tolist() == ["A", "B", ""]
    assert df["Količina"].tolist() == [1, 0, 0]
    assert df["Znesek"].tolist() == [0, 0, 0]
    assert df["Rabat (%)"].tolist() == [0, 0, 0]
    assert df["Neto po rabatu"].tolist() == [0, 0, 0]
