import pandas as pd
from decimal import Decimal

from wsm import utils


def test_log_price_history_avoids_duplicates(tmp_path, monkeypatch):
    df = pd.DataFrame({
        'sifra_dobavitelja': ['SUP'],
        'naziv': ['Artikel'],
        'cena_bruto': [Decimal('10')],
    })
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(utils, '_load_supplier_map', lambda path: {'SUP': {'ime': 'Test', }})
    hist_base = tmp_path / 'base.xlsx'
    utils.log_price_history(df, hist_base, suppliers_dir=tmp_path, invoice_id='abc')
    utils.log_price_history(df, hist_base, suppliers_dir=tmp_path, invoice_id='abc')
    hist_path = hist_base.parent / 'Test' / 'price_history.xlsx'
    hist = pd.read_excel(hist_path, dtype=str)
    assert len(hist) == 1

