import json
from decimal import Decimal
from pathlib import Path
import pandas as pd
from wsm.ui.review_links import _save_and_close

class DummyRoot:
    def quit(self):
        pass

def test_supplier_edit_saved_to_custom_dir(tmp_path, monkeypatch):
    df = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Item"],
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
    })
    manual_old = pd.DataFrame(columns=["sifra_dobavitelja", "naziv", "wsm_sifra", "dobavitelj", "enota_norm"])
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    base_dir = tmp_path / "suppliers"
    links_dir = base_dir / "Test"
    links_dir.mkdir(parents=True)
    links_file = links_dir / "SUP_Test_povezane.xlsx"

    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)

    _save_and_close(
        df,
        manual_old,
        wsm_df,
        links_file,
        DummyRoot(),
        "Test",
        "SUP",
        {},
        base_dir,
    )

    info_file = links_dir / "supplier.json"
    assert info_file.exists()
    data = json.loads(info_file.read_text())
    assert data["sifra"] == "SUP"
    assert data["ime"] == "Test"
