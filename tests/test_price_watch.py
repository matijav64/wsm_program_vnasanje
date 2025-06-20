import json
import pandas as pd
from wsm.ui.price_watch import _load_price_histories


def test_load_price_histories(tmp_path):
    links = tmp_path / "links"
    s1 = links / "Sup1"
    s2 = links / "Sup2"
    s1.mkdir(parents=True)
    s2.mkdir(parents=True)

    (s1 / "supplier.json").write_text(json.dumps({"sifra": "S1", "ime": "Sup1"}))
    (s2 / "supplier.json").write_text(json.dumps({"sifra": "S2", "ime": "Sup2"}))

    df1 = pd.DataFrame({
        "key": ["S1_ItemA"],
        "cena": [1],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df2 = pd.DataFrame({
        "key": ["S2_ItemB"],
        "cena": [2],
        "time": [pd.Timestamp("2023-01-02")],
    })
    df1.to_excel(s1 / "price_history.xlsx", index=False)
    df2.to_excel(s2 / "price_history.xlsx", index=False)

    items = _load_price_histories(links)
    assert set(items.keys()) == {"S1", "S2"}
    assert set(items["S1"].keys()) == {"S1 - ItemA"}
    assert set(items["S2"].keys()) == {"S2 - ItemB"}

