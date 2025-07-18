import pytest

pytest.importorskip("openpyxl")
import json
import pandas as pd
from decimal import Decimal

from wsm import utils
from wsm.ui.price_watch import _load_price_histories, clear_price_cache


def test_log_price_history_uses_service_date(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Artikel"],
            "cena_netto": [Decimal("10")],
            "total_net": [Decimal("10")],
            "kolicina_norm": [1],
            "enota_norm": ["kg"],
        }
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        utils, "_load_supplier_map", lambda path: {"SUP": {"ime": "Test"}}
    )
    base = tmp_path / "base.xlsx"
    utils.log_price_history(
        df, base, suppliers_dir=tmp_path, service_date="2023-01-01"
    )
    hist_path = base.parent / "Test" / "price_history.xlsx"
    hist = pd.read_excel(hist_path)
    assert pd.to_datetime(hist["time"]).iloc[0] == pd.Timestamp("2023-01-01")
    assert hist["service_date"].iloc[0] == "2023-01-01"


def test_load_price_histories_prefers_service_date(tmp_path, monkeypatch):
    clear_price_cache()
    links = tmp_path / "links"
    sup = links / "Sup"
    sup.mkdir(parents=True)
    (sup / "supplier.json").write_text(
        json.dumps({"sifra": "SUP", "ime": "Sup"})
    )
    df = pd.DataFrame(
        {
            "key": ["SUP_Item"],
            "line_netto": [1],
            "unit_price": [pd.NA],
            "enota_norm": ["kg"],
            "time": ["2023-02-01"],
            "service_date": ["2023-01-15"],
        }
    )
    df.to_excel(sup / "price_history.xlsx", index=False)
    monkeypatch.setattr(
        "wsm.ui.price_watch._load_supplier_map",
        lambda path: {"SUP": {"ime": "Sup"}},
    )
    items = _load_price_histories(links)
    item_df = items["SUP"]["SUP - Item"]
    assert pd.to_datetime(item_df["time"]).iloc[0] == pd.Timestamp(
        "2023-01-15"
    )
