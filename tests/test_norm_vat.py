import pytest
from wsm.supplier_store import _norm_vat


def test_norm_vat_valid():
    assert _norm_vat("si-123 456 78") == "SI12345678"


def test_norm_vat_overlong_truncated():
    assert _norm_vat("SI123456789") == "SI12345678"


@pytest.mark.parametrize("value", ["abc", "SI1234567", None])
def test_norm_vat_malformed(value):
    assert _norm_vat(value) == ""
