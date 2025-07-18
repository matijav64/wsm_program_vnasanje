from wsm.parsing.eslog import parse_invoice
from pathlib import Path


def test_missing_unit_defaults_to_kos(tmp_path):
    xml = tmp_path / "inv.xml"
    xml.write_text(
        """<?xml version="1.0"?>
    <Racun>
      <Postavka>
        <Naziv>ORZOESPRESSO 25 kos</Naziv>
        <Kolicina>1</Kolicina>
        <Cena>7.2</Cena>
      </Postavka>
    </Racun>"""
    )

    df, _, _ = parse_invoice(xml)
    assert df.loc[0, "enota"] == "kos"
