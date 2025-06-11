from decimal import Decimal
from wsm.ui.review_links import _norm_unit


def test_norm_unit_mg_unit():
    q, unit = _norm_unit(Decimal('500'), 'mg', 'Vitamin C', False)
    assert unit == 'kg'
    assert q == Decimal('0.0005')


def test_norm_unit_mg_in_name_with_kos():
    q, unit = _norm_unit(Decimal('1'), 'kos', 'Tabletka 250 mg', False)
    assert unit == 'kg'
    assert q == Decimal('0.00025')

