# flake8: noqa
"""Tests for VAT allocation across rates."""

from decimal import Decimal

from wsm.parsing.eslog import _vat_total_after_doc


def test_vat_allocation_proportional():
    lines_by_rate = {
        Decimal("9.5"): Decimal("100"),
        Decimal("22"): Decimal("200"),
    }
    vat = _vat_total_after_doc(None, lines_by_rate, Decimal("30"))
    assert vat == Decimal("48.15")
