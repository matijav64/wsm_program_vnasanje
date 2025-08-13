from pathlib import Path
from lxml import etree as LET
from wsm.parsing.eslog import _find_vat


def test_find_vat_prefers_valid_si_vat_over_numeric_code():
    xml = Path("tests/vat_with_numeric.xml")
    tree = LET.parse(xml)
    root = tree.getroot()
    assert _find_vat(root) == "SI12345678"
