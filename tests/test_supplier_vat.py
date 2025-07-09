from pathlib import Path
from wsm.parsing.eslog import get_supplier_info_vat


def test_get_supplier_info_vat_prefers_seller():
    xml = Path("tests/PR5918-Slika2.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI29746507"


def test_get_supplier_info_vat_prefers_va_over_ahp():
    xml = Path("tests/vat_ahp_before_va.xml")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI22222222"


def test_get_supplier_info_vat_with_gln():
    xml = Path("tests/vat_with_gln.xml")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI33333333"
