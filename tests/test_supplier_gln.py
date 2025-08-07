from pathlib import Path
from lxml import etree as LET

from wsm.parsing.eslog import get_supplier_info


def test_get_supplier_info_prefers_vat_over_gln():
    xml = Path("tests/vat_with_gln.xml")
    tree = LET.parse(xml)
    code = get_supplier_info(tree)
    assert code == "SI33333333"


def test_get_supplier_info_uses_gln_when_vat_missing():
    xml = Path("tests/gln_only.xml")
    tree = LET.parse(xml)
    code = get_supplier_info(tree)
    assert code == "9876543210987"
