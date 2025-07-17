from decimal import Decimal
from wsm.ui.review.helpers import _apply_price_warning


def test_apply_price_warning_none():
    warn, tooltip = _apply_price_warning(Decimal("1"), None)
    assert warn is False
    assert tooltip is None


def test_apply_price_warning_within_threshold():
    warn, tooltip = _apply_price_warning(
        Decimal("10.01"), Decimal("10"), threshold=Decimal("5")
    )
    assert warn is False
    assert tooltip == ""


def test_apply_price_warning_exceeds_threshold():
    warn, tooltip = _apply_price_warning(
        Decimal("11"), Decimal("10"), threshold=Decimal("5")
    )
    assert warn is True
    assert tooltip == "±1.00 €"
