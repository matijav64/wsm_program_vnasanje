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


def test_get_supplier_info_prefers_vat_over_gln():
    xml = Path("tests/PR5690-Slika1.XML")
    code, _ = get_supplier_info(xml)
    assert code == "1121499"


def test_get_supplier_info_uses_vat_when_no_gln():
    xml = Path("tests/vat_ahp_before_va.xml")
    code, _ = get_supplier_info(xml)
    assert code == "si 22222222"


def test_get_supplier_info_vat_handles_plain_rff():
    xml = Path("tests/Racun_st._25-24412.xml")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI47083026"


def test_get_supplier_info_vat_reads_ubl_vat():
    xml = Path("tests/ubl_vat.xml")
    code, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI99999999"
    assert code == "SI99999999"


def test_get_supplier_info_vat_reads_ubl_va_scheme():
    xml = Path("tests/ubl_vat_va.xml")
    code, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI69092958"
    assert code == "SI69092958"
