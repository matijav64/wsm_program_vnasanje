from pathlib import Path
from wsm.parsing.eslog import get_supplier_info_vat


def test_get_supplier_info_vat_prefers_seller():
    xml = Path("tests/PR5918-Slika2.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI29746507"


def test_get_supplier_info_vat_uses_se_when_su_missing():
    xml = Path("tests/SE_after_SU.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI11111111"
