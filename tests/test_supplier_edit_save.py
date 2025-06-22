import json
from decimal import Decimal
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
    links_dir = base_dir / "SI999"
    links_dir.mkdir(parents=True)
    links_file = links_dir / "SUP_SI999_povezane.xlsx"

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
        vat="SI999",
    )

    info_file = links_dir / "supplier.json"
    assert info_file.exists()
    data = json.loads(info_file.read_text())
    assert data["sifra"] == "SUP"
    assert data["ime"] == "Test"
    assert data["vat"] == "SI999"


def test_supplier_folder_renamed_on_vat_change(tmp_path, monkeypatch):
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
        "dobavitelj": ["Old"],
        "kolicina_norm": [1.0],
        "enota_norm": ["kg"],
    })

    manual_old = pd.DataFrame(columns=["sifra_dobavitelja", "naziv", "wsm_sifra", "dobavitelj", "enota_norm"])
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    base_dir = tmp_path / "suppliers"
    old_dir = base_dir / "Old"
    old_dir.mkdir(parents=True)
    links_file = old_dir / "SUP_Old_povezane.xlsx"
    df.to_excel(links_file, index=False)

    sup_map = {"SUP": {"ime": "Old", "vat": ""}}

    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)

    _save_and_close(
        df,
        manual_old,
        wsm_df,
        links_file,
        DummyRoot(),
        "New",
        "SUP",
        sup_map,
        base_dir,
        vat="SI111",
    )

    new_dir = base_dir / "SI111"
    assert new_dir.exists()
    assert not old_dir.exists()
    new_file = new_dir / "SUP_SI111_povezane.xlsx"
    assert new_file.exists()
    info_file = new_dir / "supplier.json"
    assert json.loads(info_file.read_text())["vat"] == "SI111"


