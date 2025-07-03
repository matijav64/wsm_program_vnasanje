from decimal import Decimal
import pandas as pd
from wsm import analyze


def test_gratis_lines_are_removed(monkeypatch):
    data = pd.DataFrame([
        {
            'sifra_dobavitelja': 'SUP',
            'naziv': 'Kava',
            'kolicina': Decimal('24'),
            'enota': 'kos',
            'cena_bruto': Decimal('0'),
            'cena_netto': Decimal('0'),
            'rabata': Decimal('0'),
            'rabata_pct': Decimal('0'),
            'vrednost': Decimal('48'),
            'sifra_artikla': '111',
            'ddv_stopnja': Decimal('22'),
        },
        {
            'sifra_dobavitelja': 'SUP',
            'naziv': 'Kava',
            'kolicina': Decimal('6'),
            'enota': 'kos',
            'cena_bruto': Decimal('0'),
            'cena_netto': Decimal('0'),
            'rabata': Decimal('12'),
            'rabata_pct': Decimal('100'),
            'vrednost': Decimal('0'),
            'sifra_artikla': '111',
            'ddv_stopnja': Decimal('22'),
        },
    ])

    monkeypatch.setattr(analyze, 'parse_eslog_invoice', lambda path, sup: data)
    monkeypatch.setattr(analyze, '_norm_unit', lambda q, u, n, vat=None, code=None: (q, u))
    monkeypatch.setattr(analyze, 'extract_header_net', lambda path: Decimal('48'))

    df, total, ok = analyze.analyze_invoice('dummy.xml')

    assert total == Decimal('48')
    assert ok
    rows = df[df['sifra_artikla'] == '111']
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row['kolicina'] == Decimal('24')
    assert row['vrednost'] == Decimal('48')

