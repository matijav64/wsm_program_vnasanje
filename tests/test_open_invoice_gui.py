import pandas as pd
from decimal import Decimal
from pathlib import Path

from wsm.ui.common import open_invoice_gui


def test_open_invoice_gui_handles_base_dir_error(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.xml"
    invoice.write_text("<xml/>")

    suppliers_dir = tmp_path / "links"

    def fake_analyze(inv, suppliers_file):
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
    monkeypatch.setattr(
        "wsm.ui.common.pd.read_excel", lambda *a, **k: pd.DataFrame()
    )
    monkeypatch.setattr("wsm.ui.common.review_links", lambda *a, **k: None)
    monkeypatch.setattr("wsm.utils.povezi_z_wsm", lambda df, *a, **k: df)
    monkeypatch.setattr("wsm.utils.main_supplier_code", lambda df: "SUP")
    monkeypatch.setattr("wsm.ui.common.get_supplier_name", lambda p: "Unknown")
    monkeypatch.setattr("wsm.ui.common._load_supplier_map", lambda p: {})
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)

    captured = {}

    def fake_showerror(title, msg):
        captured["msg"] = msg

    monkeypatch.setattr("tkinter.messagebox.showerror", fake_showerror)

    def raise_error(self, *a, **k):
        raise FileNotFoundError("cannot create")

    monkeypatch.setattr(Path, "mkdir", raise_error)

    open_invoice_gui(invoice_path=invoice, suppliers=suppliers_dir)

    assert "ni mogo\u010de ustvariti" in captured["msg"]
