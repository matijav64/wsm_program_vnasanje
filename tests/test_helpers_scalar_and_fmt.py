import pandas as pd
from decimal import Decimal
from wsm.ui.review.helpers import _fmt, _first_scalar


def test_fmt_with_series_and_none():
    assert _fmt(pd.Series([Decimal("1.50")])) == "1.5"
    assert _fmt(pd.Series([None])) == ""


def test_fmt_with_bools():
    assert _fmt(True) == "1"
    assert _fmt(False) == "0"


def test_first_scalar_basic():
    s = pd.Series([None, "X"]).replace({None: pd.NA})
    assert _first_scalar(s) == "X"
    assert _first_scalar(pd.Series([], dtype="object")) is None
