import pandas as pd
from decimal import Decimal
from pathlib import Path
from click.testing import CliRunner

import wsm.cli as cli
from wsm.ui.common import open_invoice_gui
from wsm.utils import sanitize_folder_name


def test_cli_analyze_reads_env_suppliers(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links_env"
    monkeypatch.setenv("WSM_SUPPLIERS", str(suppliers_dir))

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = suppliers_file
        return pd.DataFrame(), Decimal("0"), True

    monkeypatch.setattr(cli, "analyze_invoice", fake_analyze)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["analyze", str(invoice)])
    assert result.exit_code == 0
    assert captured["sup"] == str(suppliers_dir)


def test_cli_review_uses_env_vars(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links_env"
    codes_file = tmp_path / "codes.xlsx"
    codes_file.write_text("dummy")

    keywords_file = tmp_path / "kw.xlsx"
    keywords_file.write_text("dummy")

    monkeypatch.setenv("WSM_SUPPLIERS", str(suppliers_dir))
    monkeypatch.setenv("WSM_CODES", str(codes_file))
    monkeypatch.setenv("WSM_KEYWORDS", str(keywords_file))

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = suppliers_file
        df = pd.DataFrame({
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kos"],
            "vrednost": [Decimal("1")],
            "rabata": [Decimal("0")],
        })
        return df, Decimal("1"), True

    def fake_read_excel(path, dtype=None):
        captured["codes"] = Path(path)
        return pd.DataFrame()

    def fake_review_links(df, wsm_df, links_file, total, invoice_path):
        captured["links"] = links_file

    def fake_povezi(df, sifre, keywords_path=None, links_dir=None, supplier_code=None):
        captured["kw"] = Path(keywords_path)
        return df

    monkeypatch.setattr(cli, "analyze_invoice", fake_analyze)
    monkeypatch.setattr(cli.pd, "read_excel", fake_read_excel)
    monkeypatch.setattr("wsm.ui.review_links.review_links", fake_review_links)
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", fake_povezi)
    monkeypatch.setattr(cli, "get_supplier_name", lambda p: "Test Supplier")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["review", str(invoice)])
    assert result.exit_code == 0

    expected = suppliers_dir / sanitize_folder_name("Test Supplier") / "SUP_Test Supplier_povezane.xlsx"
    assert captured["sup"] == str(suppliers_dir)
    assert captured["codes"] == codes_file
    assert captured["links"] == expected
    assert captured["kw"] == keywords_file


def test_open_invoice_gui_uses_env_vars(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links_env"
    codes_file = tmp_path / "codes.xlsx"
    codes_file.write_text("dummy")

    keywords_file = tmp_path / "kw.xlsx"
    keywords_file.write_text("dummy")

    monkeypatch.setenv("WSM_SUPPLIERS", str(suppliers_dir))
    monkeypatch.setenv("WSM_CODES", str(codes_file))
    monkeypatch.setenv("WSM_KEYWORDS", str(keywords_file))

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = Path(suppliers_file)
        df = pd.DataFrame({
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kos"],
            "vrednost": [Decimal("1")],
            "rabata": [Decimal("0")],
        })
        return df, Decimal("1"), True

    def fake_read_excel(path, dtype=None):
        captured["codes"] = Path(path)
        return pd.DataFrame()

    def fake_review_links(df, wsm_df, links_file, total, invoice_path):
        captured["links"] = links_file

    def fake_povezi(df, sifre, keywords_path=None, links_dir=None, supplier_code=None):
        captured["kw"] = Path(keywords_path)
        return df

    monkeypatch.setattr("wsm.ui.common.analyze_invoice", fake_analyze)
    monkeypatch.setattr("wsm.ui.common.pd.read_excel", fake_read_excel)
    monkeypatch.setattr("wsm.ui.common.review_links", fake_review_links)
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", fake_povezi)
    monkeypatch.setattr("wsm.ui.common.get_supplier_name", lambda p: "Test Supplier")

    open_invoice_gui(invoice_path=invoice)

    expected = suppliers_dir / sanitize_folder_name("Test Supplier") / "SUP_Test Supplier_povezane.xlsx"
    assert captured["sup"] == suppliers_dir
    assert captured["codes"] == codes_file
    assert captured["links"] == expected
    assert captured["kw"] == keywords_file
