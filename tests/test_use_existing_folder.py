import pandas as pd
from decimal import Decimal
from pathlib import Path
from wsm.ui.common import open_invoice_gui


def test_open_invoice_gui_uses_existing_folder(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links"
    old_dir = suppliers_dir / "unknown"
    old_dir.mkdir(parents=True)
    (old_dir / "SUP_unknown_povezane.xlsx").write_text("dummy")

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = Path(suppliers_file)
        df = pd.DataFrame(
            {
                "sifra_dobavitelja": ["SUP"],
                "naziv": ["Item"],
                "kolicina": [Decimal("1")],
                "enota": ["kos"],
                "vrednost": [Decimal("1")],
                "rabata": [Decimal("0")],
            }
        )
        return df, Decimal("1"), True

    monkeypatch.setattr("wsm.ui.common.analyze_invoice", fake_analyze)
    monkeypatch.setattr("wsm.ui.common.pd.read_excel", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("wsm.ui.common.review_links", lambda df, wdf, lf, total, ip: captured.update({"links": lf}))
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", lambda df, *a, **k: df)
    monkeypatch.setattr("wsm.ui.common.get_supplier_name", lambda p: "Unknown")
    monkeypatch.setattr("wsm.parsing.eslog.get_supplier_info_vat", lambda p: ("", "", "SI111"))
    monkeypatch.setattr("wsm.ui.common._load_supplier_map", lambda p: {"SUP": {"ime": "unknown", "vat": ""}})

    open_invoice_gui(invoice_path=invoice, suppliers=suppliers_dir)

    expected = suppliers_dir / "SUP" / "SUP_SUP_povezane.xlsx"
    assert captured["links"] == expected


def test_open_invoice_gui_prefers_vat_folder(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links"

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = Path(suppliers_file)
        df = pd.DataFrame(
            {
                "sifra_dobavitelja": ["SUP"],
                "naziv": ["Item"],
                "kolicina": [Decimal("1")],
                "enota": ["kos"],
                "vrednost": [Decimal("1")],
                "rabata": [Decimal("0")],
            }
        )
        return df, Decimal("1"), True

    monkeypatch.setattr("wsm.ui.common.analyze_invoice", fake_analyze)
    monkeypatch.setattr("wsm.ui.common.pd.read_excel", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        "wsm.ui.common.review_links",
        lambda df, wdf, lf, total, ip: captured.update({"links": lf}),
    )
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", lambda df, *a, **k: df)
    monkeypatch.setattr("wsm.ui.common.get_supplier_name", lambda p: "Unknown")
    monkeypatch.setattr(
        "wsm.parsing.eslog.get_supplier_info_vat", lambda p: ("", "", "SI111")
    )
    monkeypatch.setattr("wsm.ui.common._load_supplier_map", lambda p: {})

    open_invoice_gui(invoice_path=invoice, suppliers=suppliers_dir)

    expected_dir = suppliers_dir / "SUP"
    expected = expected_dir / "SUP_SUP_povezane.xlsx"
    assert captured["links"] == expected
    assert expected_dir.exists()


def test_open_invoice_gui_uses_vat_from_map(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links"

    captured = {}

    def fake_analyze(inv, suppliers_file):
        captured["sup"] = Path(suppliers_file)
        df = pd.DataFrame(
            {
                "sifra_dobavitelja": ["SUP"],
                "naziv": ["Item"],
                "kolicina": [Decimal("1")],
                "enota": ["kos"],
                "vrednost": [Decimal("1")],
                "rabata": [Decimal("0")],
            }
        )
        return df, Decimal("1"), True

    monkeypatch.setattr("wsm.ui.common.analyze_invoice", fake_analyze)
    monkeypatch.setattr("wsm.ui.common.pd.read_excel", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        "wsm.ui.common.review_links",
        lambda df, wdf, lf, total, ip: captured.update({"links": lf}),
    )
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", lambda df, *a, **k: df)
    monkeypatch.setattr("wsm.ui.common.get_supplier_name", lambda p: "Unknown")
    monkeypatch.setattr("wsm.ui.common._load_supplier_map", lambda p: {"SUP": {"ime": "Unknown", "vat": "SI222"}})

    open_invoice_gui(invoice_path=invoice, suppliers=suppliers_dir)

    expected_dir = suppliers_dir / "SI222"
    expected = expected_dir / "SUP_SI222_povezane.xlsx"
    assert captured["links"] == expected
    assert expected_dir.exists()
