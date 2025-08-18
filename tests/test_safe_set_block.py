import pandas as pd

from wsm.ui.review.helpers import _safe_set_block


def test_safe_set_block_empty_frame():
    df = pd.DataFrame()
    result = _safe_set_block(df, ["A", "B"], [])
    assert result.empty
    assert list(result.columns) == ["A", "B"]


def test_safe_set_block_mismatched_list_lengths():
    df = pd.DataFrame(index=[0, 1])
    result = _safe_set_block(df, ["A", "B"], [pd.Series([1, 2, 3])])
    assert (result[["A", "B"]] == 0).all().all()


def test_safe_set_block_scalar_input():
    df = pd.DataFrame({"existing": [0, 0, 0]})
    result = _safe_set_block(df, ["A", "B"], 5)
    assert (result[["A", "B"]] == 5).all().all()
