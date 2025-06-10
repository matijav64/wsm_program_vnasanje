from pathlib import Path
from wsm.parsing.eslog import extract_service_date


def test_extract_service_date_delivery():
    xml = Path("tests/CUSTOMERINVOICES_2025-04-01T14-29-47_2081078.xml")
    assert extract_service_date(xml) == "2025-03-31"


def test_extract_service_date_fallback():
    xml = Path("tests/VP2025-1799-racun.xml")
    assert extract_service_date(xml) == "2025-03-06"
