from pathlib import Path
from wsm.parsing.eslog import parse_eslog_invoice


def test_parse_eslog_invoice_uses_ahp_when_no_va():
    xml = Path(__file__).with_suffix("").with_name("vat_ahp_only.xml")
    df, ok = parse_eslog_invoice(xml)
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
    assert not df.empty
    assert set(df["sifra_dobavitelja"]) == {"SI76543210"}
    assert ok
