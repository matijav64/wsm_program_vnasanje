import pytest
from pathlib import Path
from wsm.parsing.eslog import parse_invoice, validate_invoice

@pytest.mark.parametrize('xml_file', [
    'PR5697-Slika2.XML',  # contains document-level discount
    '2025-581-racun.xml',  # another with document discount
])
def test_validate_invoice_with_doc_discount(xml_file):
    df, header_total = parse_invoice(Path('tests') / xml_file)
    assert validate_invoice(df, header_total)


def test_validate_invoice_no_doc_discount():
    df, header_total = parse_invoice(Path('tests') / 'PR5690-Slika1.XML')
    assert validate_invoice(df, header_total)

