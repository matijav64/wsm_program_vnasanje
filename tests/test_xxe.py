from wsm.parsing import eslog


def test_external_entities_are_ignored(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("LEAK")
    xml = tmp_path / "evil.xml"
    xml.write_text(
        f"""<!DOCTYPE Invoice [<!ENTITY ext SYSTEM '{secret.as_uri()}'>]>
<Invoice xmlns='urn:eslog:2.00'>
  <M_INVOIC>&ext;</M_INVOIC>
</Invoice>"""
    )
    df, ok = eslog.parse_eslog_invoice(xml)
    assert df.empty
    assert ok
    root = eslog.LET.parse(xml, parser=eslog.XML_PARSER).getroot()
    assert "LEAK" not in "".join(root.itertext())
