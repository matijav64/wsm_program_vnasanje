import pytest

pytest.importorskip("openpyxl")
import json
from decimal import Decimal
import pandas as pd
from wsm.ui.review.io import _save_and_close


class DummyRoot:
    def quit(self):
        pass


def test_supplier_edit_saved_to_custom_dir(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
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
        }
    )
    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "enota_norm",
        ]
    )
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
    df = pd.DataFrame(
        {
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
        }
    )

    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "enota_norm",
        ]
    )
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


def test_unknown_folder_removed_when_vat_exists(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kg"],
            "cena_bruto": [Decimal("5")],
            "cena_netto": [Decimal("5")],
            "vrednost": [Decimal("5")],
            "rabata": [Decimal("0")],
            "wsm_sifra": [pd.NA],
            "dobavitelj": ["Unknown"],
            "kolicina_norm": [1.0],
            "enota_norm": ["kg"],
        }
    )

    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "enota_norm",
        ]
    )
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    base_dir = tmp_path / "suppliers"
    old_dir = base_dir / "unknown"
    old_dir.mkdir(parents=True)
    links_file = old_dir / "SUP_unknown_povezane.xlsx"
    links_file.write_text("dummy")
    extra = old_dir / "extra.txt"
    extra.write_text("x")

    new_dir = base_dir / "SI111"
    new_dir.mkdir(parents=True)
    (new_dir / "SUP_SI111_povezane.xlsx").write_text("existing")

    sup_map = {"SUP": {"ime": "Unknown", "vat": ""}}

    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)

    _save_and_close(
        df,
        manual_old,
        wsm_df,
        links_file,
        DummyRoot(),
        "Unknown",
        "SUP",
        sup_map,
        base_dir,
        vat="SI111",
    )

    assert not old_dir.exists()
    assert new_dir.exists()
    files = {p.name for p in new_dir.iterdir()}
    assert "extra.txt" in files or "extra_old.txt" in files
    assert "SUP_SI111_povezane.xlsx" in files


def test_unknown_folder_cleaned_after_save(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
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
        }
    )

    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "enota_norm",
        ]
    )
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    base_dir = tmp_path / "suppliers"
    unknown_dir = base_dir / "unknown"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "junk.txt").write_text("x")

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

    assert not unknown_dir.exists()
