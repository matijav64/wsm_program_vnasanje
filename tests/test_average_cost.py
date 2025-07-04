from decimal import Decimal
import pandas as pd
from wsm import utils


def _sample_df():
    return pd.DataFrame({
        "cena_netto": [Decimal("10"), Decimal("0")],
        "kolicina": [Decimal("1"), Decimal("1")],
    })


def test_average_cost_skip_zero(monkeypatch):
    df = _sample_df()
    monkeypatch.setenv("AVG_COST_SKIP_ZERO", "1")
    avg = utils.average_cost(df)
    assert avg == Decimal("10.0000")


def test_average_cost_include_zero(monkeypatch):
    df = _sample_df()
    monkeypatch.delenv("AVG_COST_SKIP_ZERO", raising=False)
    avg = utils.average_cost(df)
    assert avg == Decimal("5.0000")
