import json
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from wsm.cli import main
from wsm import analyze


XML = (
    "<Invoice xmlns='urn:eslog:2.00'>"
    "  <M_INVOIC>"
    "    <G_SG2>"
    "      <S_NAD>"
    "        <D_3035>SE</D_3035>"
    "        <C_C082><D_3039>SUP</D_3039></C_C082>"
    "        <C_C080><D_3036>Test</D_3036></C_C080>"
    "      </S_NAD>"
    "    </G_SG2>"
    "    <G_SG26>"
    "      <S_QTY><C_C186><D_6060>2.5</D_6060><D_6411>H87</D_6411></C_C186></S_QTY>"
    "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
    "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
    "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>4</D_5118></C_C509></S_PRI>"
    "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
    "    </G_SG26>"
    "    <G_SG50>"
    "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
    "    </G_SG50>"
    "  </M_INVOIC>"
    "</Invoice>"
)


def test_cli_override(tmp_path):
    links = tmp_path / "links"
    supplier_dir = links / "Test"
    supplier_dir.mkdir(parents=True)

    info = {"sifra": "SUP", "ime": "Test", "override_H87_to_kg": False}
    (supplier_dir / "supplier.json").write_text(json.dumps(info))

    runner = CliRunner()
    result = runner.invoke(main, [
        "override", "SUP", "--suppliers", str(links), "--set"
    ])
    assert result.exit_code == 0

    data = json.loads((supplier_dir / "supplier.json").read_text())
    assert data["override_H87_to_kg"] is True

    xml_path = tmp_path / "invoice.xml"
    xml_path.write_text(XML)

    df, total, ok = analyze.analyze_invoice(xml_path, str(links))
    row = df[df["sifra_dobavitelja"] == "SUP"].iloc[0]
    assert row["enota"] == "kg"
    assert row["kolicina"] == Decimal("2.5")
    assert total == Decimal("10")
    assert ok


def test_cli_override_creates_dir(tmp_path):
    links = tmp_path / "links"

    runner = CliRunner()
    result = runner.invoke(main, ["override", "SUP", "--suppliers", str(links), "--set"])
    assert result.exit_code == 0

    supplier_json = links / "SUP" / "supplier.json"
    assert supplier_json.exists()

    xml_path = tmp_path / "invoice.xml"
    xml_path.write_text(XML)

    df, total, ok = analyze.analyze_invoice(xml_path, str(links))
    row = df[df["sifra_dobavitelja"] == "SUP"].iloc[0]
    assert row["enota"] == "kg"
    assert row["kolicina"] == Decimal("2.5")
    assert total == Decimal("10")
    assert ok
