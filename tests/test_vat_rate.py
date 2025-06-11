from decimal import Decimal
from pathlib import Path
from wsm.parsing.eslog import parse_eslog_invoice


def test_vat_rate_22():
    df = parse_eslog_invoice(Path('tests/VP2025-1799-racun.xml'), {})
    df = df[df['sifra_dobavitelja'] != '_DOC_']
    assert 'ddv_stopnja' in df.columns
    assert df['ddv_stopnja'].iloc[0] == Decimal('22.00')


def test_vat_rate_9_5():
    df = parse_eslog_invoice(Path('tests/PR5691-Slika2.XML'), {})
    df = df[df['sifra_dobavitelja'] != '_DOC_']
    assert set(df['ddv_stopnja'].unique()) == {Decimal('9.5')}
