import pandas as pd
from decimal import Decimal

from wsm.ui.review_links import _save_and_close

class DummyRoot:
    def quit(self):
        pass


def _sample_df():
    return pd.DataFrame(
        {
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Artikel"],
            "kolicina": [Decimal("1")],
            "enota": ["kg"],
            "cena_bruto": [Decimal("5")],
            "cena_netto": [Decimal("5")],
            "vrednost": [Decimal("5")],
            "rabata": [Decimal("0")],
            "wsm_sifra": [pd.NA],
            "dobavitelj": ["Test"],
            "kolicina_norm": [1.0],
            "enota_norm": ["kg"],
        }
    )


def test_duplicate_invoice_warning(tmp_path, monkeypatch):
    df = _sample_df()
    manual_old = pd.DataFrame(
        columns=["sifra_dobavitelja", "naziv", "wsm_sifra", "dobavitelj", "enota_norm"]
    )
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    base_dir = tmp_path / "links"
    first_dir = base_dir / "Test"
    first_dir.mkdir(parents=True)
    links_file = first_dir / "SUP_Test_povezane.xlsx"

    invoice_path = tmp_path / "inv.xml"
    invoice_path.write_text("<xml></xml>")

    sup_map = {}

    _save_and_close(
        df,
        manual_old,
        wsm_df,
        links_file,
        DummyRoot(),
        "Test",
        "SUP",
        sup_map,
        base_dir,
        invoice_path=invoice_path,
        vat="SI123",
    )

    links_file = base_dir / "SI123" / "SUP_SI123_povezane.xlsx"

    calls = []
    monkeypatch.setattr(
        "tkinter.messagebox.askyesno", lambda *a, **k: (calls.append(True) or False)
    )
    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: calls.append("log"))

    _save_and_close(
        df,
        manual_old,
        wsm_df,
        links_file,
        DummyRoot(),
        "Test",
        "SUP",
        sup_map,
        base_dir,
        invoice_path=invoice_path,
        vat="SI123",
    )

    assert calls == [True]
