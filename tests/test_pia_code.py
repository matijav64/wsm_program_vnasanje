from decimal import Decimal
from pathlib import Path

from wsm import analyze
from wsm.parsing.eslog import parse_eslog_invoice


def test_parse_eslog_invoice_reads_code_from_pia():
    xml = Path('tests/VP2025-1799-racun.xml')
    df, ok = parse_eslog_invoice(xml)
    df = df[df['sifra_dobavitelja'] != '_DOC_']
    assert list(df['sifra_artikla']) == [
        '4025127091881',
        '4025127088942',
        '4025127014002',
    ]
    assert ok


def test_analyze_invoice_groups_duplicate_pia_lines(tmp_path, monkeypatch):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><D_1082>1</D_1082></S_LIN>"
        "      <S_PIA><C_C212><D_7140>111</D_7140></C_C212></S_PIA>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><D_1082>2</D_1082></S_LIN>"
        "      <S_PIA><C_C212><D_7140>111</D_7140></C_C212></S_PIA>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / 'invoice.xml'
    xml_path.write_text(xml)

    monkeypatch.setattr(analyze, 'extract_header_net', lambda p: Decimal('20'))
    monkeypatch.setattr(analyze, '_norm_unit', lambda q, u, n, vat=None, code=None: (q, u))

    df, total, ok = analyze.analyze_invoice(xml_path)
    df = df[df['sifra_dobavitelja'] != '_DOC_']

    assert len(df) == 1
    row = df.iloc[0]
    assert row['sifra_artikla'] == '111'
    assert row['kolicina'] == Decimal('2')
    assert row['vrednost'] == Decimal('20')
    assert total == Decimal('20')
    assert ok
