import pandas as pd
from decimal import Decimal
from wsm import analyze


def test_grouping_by_code_and_discount(monkeypatch):
    # Prepare synthetic invoice DataFrame
    data = pd.DataFrame([
        {
            'sifra_dobavitelja': 'SUP',
            'naziv': 'Artikel A',
            'kolicina': Decimal('2'),
            'enota': 'kos',
            'cena_bruto': Decimal('0'),
            'cena_netto': Decimal('0'),
            'rabata': Decimal('0'),
            'rabata_pct': Decimal('5'),
            'vrednost': Decimal('10'),
            'sifra_artikla': '001',
            'ddv_stopnja': Decimal('22'),
        },
        {
            'sifra_dobavitelja': 'SUP',
            'naziv': 'Artikel A drugačen opis',
            'kolicina': Decimal('3'),
            'enota': 'kos',
            'cena_bruto': Decimal('0'),
            'cena_netto': Decimal('0'),
            'rabata': Decimal('0'),
            'rabata_pct': Decimal('5'),
            'vrednost': Decimal('15'),
            'sifra_artikla': '001',
            'ddv_stopnja': Decimal('22'),
        },
        {
            'sifra_dobavitelja': 'SUP',
            'naziv': 'Artikel A 10%',
            'kolicina': Decimal('1'),
            'enota': 'kos',
            'cena_bruto': Decimal('0'),
            'cena_netto': Decimal('0'),
            'rabata': Decimal('0'),
            'rabata_pct': Decimal('10'),
            'vrednost': Decimal('5'),
            'sifra_artikla': '001',
            'ddv_stopnja': Decimal('22'),
        },
    ])

    # Patch parse_eslog_invoice to return our DataFrame
    monkeypatch.setattr(analyze, 'parse_eslog_invoice', lambda path, sup: data)
    # Identity normalization
    monkeypatch.setattr(analyze, '_norm_unit', lambda q, u, n, vat=None: (q, u))
    # Header total equals sum of values
    monkeypatch.setattr(analyze, 'extract_header_net', lambda path: Decimal('30'))

    df, header_total, ok = analyze.analyze_invoice('dummy.xml')

    # Should merge first two rows (same code and discount)
    merged = df[df['rabata_pct'] == Decimal('5')].iloc[0]
    assert merged['kolicina'] == Decimal('5')
    assert merged['vrednost'] == Decimal('25')
    assert merged['naziv'] == 'Artikel A'
    assert merged['rabata'] == Decimal('0')

    assert 'rabata' in df.columns
    assert df['rabata'].isna().sum() == 0

    # Row with different discount should remain separate
    assert (df['rabata_pct'] == Decimal('10')).sum() == 1
    assert header_total == Decimal('30')
    assert ok
