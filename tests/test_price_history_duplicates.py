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
    assert {'code', 'name', 'cena'}.issubset(set(hist.columns))


def test_log_price_history_folder_vat(tmp_path, monkeypatch):
    df = pd.DataFrame({
        'sifra_dobavitelja': ['SUP'],
        'naziv': ['Artikel'],
        'cena_bruto': [Decimal('10')],
    })
    links_dir = tmp_path / 'links'
    history_file = links_dir / 'SI999' / 'SUP_SI999_povezane.xlsx'
    history_file.parent.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(utils, '_load_supplier_map', lambda path: {'SUP': {'ime': 'Test', 'vat': ''}})
    utils.log_price_history(df, history_file, suppliers_dir=links_dir)
    hist_path = links_dir / 'SI999' / 'price_history.xlsx'
    hist = pd.read_excel(hist_path, dtype=str)
    assert not hist.empty
    assert {'code', 'name', 'cena'}.issubset(set(hist.columns))
