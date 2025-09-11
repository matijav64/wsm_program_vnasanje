import pandas as pd
from wsm.ui.review.helpers import first_existing, first_existing_series


def test_first_existing_duplicate_columns():
    df = pd.DataFrame([[1, 2], [None, 3]], columns=["x", "x"])
    result = first_existing(df, ["x"])
    assert isinstance(result, pd.Series)
    assert result.tolist() == [1, 0]

    result_series = first_existing_series(df, ["x"])
    assert isinstance(result_series, pd.Series)
    assert result_series.tolist() == [1, 0]
