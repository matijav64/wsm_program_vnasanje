from pathlib import Path
from wsm.parsing.eslog import parse_eslog_invoice


def test_mismatch_grand_total():
    xml_path = Path("tests/mismatch_grand_total.xml")
    df, ok = parse_eslog_invoice(xml_path)
    assert not ok
