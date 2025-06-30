from decimal import Decimal
import pandas as pd
from wsm.utils import last_price_stats, load_last_price


def test_last_price_stats_basic():
    df = pd.DataFrame({
        "cena": [Decimal("1"), Decimal("2"), Decimal("1.5")],
        "time": [
            pd.Timestamp("2023-01-01"),
            pd.Timestamp("2023-01-02"),
            pd.Timestamp("2023-01-03"),
        ],
    })
    stats = last_price_stats(df)
    assert stats["last_price"] == Decimal("1.5")
    assert stats["last_dt"] == pd.Timestamp("2023-01-03")
    assert stats["min"] == Decimal("1")
    assert stats["max"] == Decimal("2")


def test_last_price_stats_missing_columns():
    df = pd.DataFrame({"cena": [1]})
    assert last_price_stats(df) == {
        "last_price": None,
        "last_dt": None,
        "min": None,
        "max": None,
    }


def test_load_last_price_multiple_suppliers(tmp_path):
    links = tmp_path / "links"
    s1 = links / "S1"
    s2 = links / "S2"
    s1.mkdir(parents=True)
    s2.mkdir(parents=True)
    df1 = pd.DataFrame({
        "key": ["A_Item"],
        "code": ["A"],
        "name": ["Item"],
        "line_netto": [1],
        "unit_price": [pd.NA],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df2 = pd.DataFrame({
        "key": ["A_Item"],
        "code": ["A"],
        "name": ["Item"],
        "line_netto": [2],
        "unit_price": [pd.NA],
        "time": [pd.Timestamp("2023-02-01")],
    })
    df1.to_excel(s1 / "price_history.xlsx", index=False)
    df2.to_excel(s2 / "price_history.xlsx", index=False)
    price = load_last_price("A - Item", links)
    assert price == Decimal("2")


def test_load_last_price_multiple_suppliers_legacy(tmp_path):
    """Legacy files with column 'cena' are still supported."""
    links = tmp_path / "links"
    s1 = links / "S1"
    s2 = links / "S2"
    s1.mkdir(parents=True)
    s2.mkdir(parents=True)
    df1 = pd.DataFrame({
        "key": ["A_Item"],
        "code": ["A"],
        "name": ["Item"],
        "cena": [1],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df2 = pd.DataFrame({
        "key": ["A_Item"],
        "code": ["A"],
        "name": ["Item"],
        "cena": [2],
        "time": [pd.Timestamp("2023-02-01")],
    })
    df1.to_excel(s1 / "price_history.xlsx", index=False)
    df2.to_excel(s2 / "price_history.xlsx", index=False)
    price = load_last_price("A - Item", links)
    assert price == Decimal("2")


def test_load_last_price_missing_file(tmp_path):
    links = tmp_path / "links"
    links.mkdir()
    assert load_last_price("X - Item", links) is None


def test_load_last_price_missing_columns(tmp_path):
    links = tmp_path / "links"
    sup = links / "S1"
    sup.mkdir(parents=True)
    pd.DataFrame({"key": ["A_Item"]}).to_excel(sup / "price_history.xlsx", index=False)
    assert load_last_price("A - Item", links) is None
