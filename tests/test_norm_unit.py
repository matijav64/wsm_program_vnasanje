from decimal import Decimal
from wsm.ui.review.helpers import _norm_unit


def test_norm_unit_mg_unit():
    q, unit = _norm_unit(
        Decimal("500"), "mg", "Vitamin C", Decimal("22"), None
    )
    assert unit == "kg"
    assert q == Decimal("0.0005")


def test_norm_unit_mg_in_name_with_kos():
    q, unit = _norm_unit(
        Decimal("1"), "kos", "Tabletka 250 mg", Decimal("9.5"), None
    )
    assert unit == "kg"
    assert q == Decimal("0.00025")


def test_norm_unit_vat_fraction_to_liter():
    q, unit = _norm_unit(
        Decimal("36"),
        "H87",
        "KREMA rast. za kuhanje  1/1",
        Decimal("9.5"),
        None,
    )
    assert unit == "L"
    assert q == Decimal("36")


def test_norm_unit_vat_default_kg():
    q, unit = _norm_unit(
        Decimal("2"), "H87", "Artikel brez mere", Decimal("9.5"), None
    )
    assert unit == "kg"
    assert q == Decimal("2")


def test_norm_unit_weight_table():
    q, unit = _norm_unit(
        Decimal("1"), "H87", "cevapcici 480g kos", Decimal("9.5"), "95308"
    )
    assert unit == "kg"
    assert q == Decimal("0.48")


def test_norm_unit_fractional_kos_default_kg():
    q, unit = _norm_unit(
        Decimal("4.2"), "H87", "Artikel brez mere", Decimal("22"), None
    )
    assert unit == "kg"
    assert q == Decimal("4.2")


def test_norm_unit_fractional_kos_to_liter():
    q, unit = _norm_unit(
        Decimal("4.2"), "H87", "Sok 500 ml", Decimal("22"), None
    )
    assert unit == "L"
    assert q == Decimal("2.1")
