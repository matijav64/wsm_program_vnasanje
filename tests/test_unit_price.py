import pandas as pd
from decimal import Decimal
from wsm import utils


def test_log_price_history_unit_price(tmp_path, monkeypatch):
    df = pd.DataFrame({
        "sifra_dobavitelja": ["SUP", "SUP"],
        "naziv": ["Artikel A", "Artikel B"],
        "cena_netto": [Decimal("2"), Decimal("3")],
        "total_net": [Decimal("20"), Decimal("12")],
        "kolicina_norm": [10, 4],
        "enota_norm": ["kg", "L"],
    })
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(utils, "_load_supplier_map", lambda path: {"SUP": {"ime": "Test"}})
    base = tmp_path / "base.xlsx"
    utils.log_price_history(df, base, suppliers_dir=tmp_path)
    hist_path = tmp_path / "Test" / "price_history.xlsx"
    hist = pd.read_excel(hist_path)
    prices = [Decimal(str(x)) for x in hist["unit_price"]]
    assert prices == [Decimal("2"), Decimal("3")]
