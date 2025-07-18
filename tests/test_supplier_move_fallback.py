import pytest

pytest.importorskip("openpyxl")
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from wsm.ui.review.io import _save_and_close
from wsm.supplier_store import load_suppliers, clear_supplier_cache


class DummyRoot:
    def quit(self):
        pass


def test_supplier_move_fallback(tmp_path, monkeypatch):
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
    (old_dir / "extra.txt").write_text("x")

    new_dir = base_dir / "SI111"
    new_dir.mkdir(parents=True)

    sup_map = {"SUP": {"ime": "Old", "vat": ""}}

    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)
    monkeypatch.setattr(
        Path, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )

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

    clear_supplier_cache()

    assert new_dir.exists()
    assert not old_dir.exists()
    moved = new_dir / "SUP_SI111_povezane.xlsx"
    assert moved.exists()

    result = load_suppliers(base_dir)
    assert result["SUP"]["vat"] == "SI111"
