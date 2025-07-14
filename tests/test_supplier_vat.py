from pathlib import Path
from wsm.parsing.eslog import get_supplier_info_vat, get_supplier_info


def test_get_supplier_info_vat_prefers_seller():
    xml = Path("tests/PR5918-Slika2.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI29746507"


def test_get_supplier_info_vat_uses_se_when_su_missing():
    xml = Path("tests/SE_after_SU.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI11111111"


def test_get_supplier_info_returns_gln_when_available():
    xml = Path("tests/PR5690-Slika1.XML")
    code, _ = get_supplier_info(xml)
    assert code == "3830029809998"


def test_get_supplier_info_uses_vat_when_no_gln():
    xml = Path("tests/vat_ahp_before_va.xml")
    code, _ = get_supplier_info(xml)
    assert code == "si 22222222"
