import pytest
pytest.importorskip("pdfplumber")
from pathlib import Path
from wsm.parsing.pdf import extract_service_date, extract_invoice_number

SAMPLE = Path('tests/sample_invoice.pdf')

def test_extract_service_date_pdf():
    assert extract_service_date(SAMPLE) == '2025-05-20'

def test_extract_invoice_number_pdf():
    assert extract_invoice_number(SAMPLE) == 'INV-001'
