from pathlib import Path
from wsm.parsing.eslog import extract_invoice_number


def test_extract_invoice_number():
    xml = Path("tests/VP2025-1799-racun.xml")
    assert extract_invoice_number(xml) == "VP2025-1799"
