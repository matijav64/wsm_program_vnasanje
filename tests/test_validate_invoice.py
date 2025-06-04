import pytest
from pathlib import Path
from wsm.parsing.eslog import parse_invoice, validate_invoice

@pytest.mark.parametrize('xml_file', [
    'PR5697-Slika2.XML',  # contains document-level discount
    '2025-581-racun.xml',  # another with document discount
])
def test_validate_invoice_with_doc_discount(xml_file):
    df, header_total, currency = parse_invoice(Path('tests') / xml_file)
    assert validate_invoice(df, header_total, currency)


def test_validate_invoice_no_doc_discount():
    df, header_total, currency = parse_invoice(Path('tests') / 'PR5690-Slika1.XML')
    assert validate_invoice(df, header_total, currency)


def test_validate_negative_invoice():
    df, header_total, currency = parse_invoice(Path('tests') / 'VP2025-1799-racun.xml')
    assert validate_invoice(df, header_total, currency)


def test_validate_currency_mismatch(tmp_path):
    src = Path('tests') / 'PR5690-Slika1.XML'
    data = src.read_text(encoding='utf-8')
    data = data.replace('<D_6345>EUR</D_6345>', '<D_6345>USD</D_6345>')
    temp = tmp_path / 'invoice_usd.xml'
    temp.write_text(data, encoding='utf-8')
    df, header_total, currency = parse_invoice(temp)
    assert not validate_invoice(df, header_total, currency)

