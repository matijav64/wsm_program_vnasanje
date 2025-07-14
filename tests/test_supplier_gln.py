from pathlib import Path
from wsm.parsing.eslog import get_supplier_info


def test_get_supplier_info_reads_sgln():
    xml = Path("tests/vat_with_gln.xml")
    code, _ = get_supplier_info(xml)
    assert code == "1234567890123"
