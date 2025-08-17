import types
from itertools import zip_longest

import wsm.ui.review.gui as gui


def _get_summary_helper():
    """Extract `_summary_df_from_records` nested in `review_links`."""
    for const in gui.review_links.__code__.co_consts:
        if (
            isinstance(const, types.CodeType)
            and const.co_name == "_summary_df_from_records"
        ):
            return types.FunctionType(
                const, gui.review_links.__globals__, "_summary_df_from_records"
            )
    raise AssertionError("_summary_df_from_records not found")


_summary_df_from_records = _get_summary_helper()


def test_summary_empty_returns_empty_df():
    df = _summary_df_from_records([])
    assert df.empty
    assert list(df.columns) == [
        "WSM šifra",
        "WSM Naziv",
        "Količina",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    ]


def test_summary_handles_mismatched_lengths():
    sifre = ["1", "2", "3"]
    nazivi = ["A", "B"]
    kolicine = [1]
    records = [
        {"WSM šifra": s, "WSM Naziv": n, "Količina": k}
        for s, n, k in zip_longest(sifre, nazivi, kolicine)
    ]
    df = _summary_df_from_records(records)
    assert df.shape == (3, 6)
    assert df["WSM šifra"].tolist() == ["1", "2", "3"]
    assert df["WSM Naziv"].tolist() == ["A", "B", ""]
    assert df["Količina"].tolist() == [1, 0, 0]
    assert df["Znesek"].tolist() == [0, 0, 0]
    assert df["Rabat (%)"].tolist() == [0, 0, 0]
    assert df["Neto po rabatu"].tolist() == [0, 0, 0]
