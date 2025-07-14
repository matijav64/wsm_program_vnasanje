from decimal import Decimal

import pandas as pd
import pytest

from wsm.ui.review.io import _save_and_close
from wsm.utils import _clean

pytest.importorskip("openpyxl")


class DummyRoot:
    def quit(self):
        pass


def test_blank_supplier_code_retains_mapping(tmp_path, monkeypatch):
    df = pd.DataFrame({
        "sifra_dobavitelja": [pd.NA],
        "naziv": ["Item"],
        "kolicina": [Decimal("1")],
        "enota": ["kg"],
        "cena_bruto": [Decimal("5")],
        "cena_netto": [Decimal("5")],
        "vrednost": [Decimal("5")],
        "rabata": [Decimal("0")],
        "wsm_sifra": ["A1"],
        "dobavitelj": ["Test"],
        "kolicina_norm": [1.0],
        "enota_norm": ["kg"],
    })
    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "enota_norm",
        ]
    )
    wsm_df = pd.DataFrame({"wsm_sifra": ["A1"], "wsm_naziv": ["Art"]})
    base_dir = tmp_path / "suppliers"
    links_dir = base_dir / "Test"
    links_dir.mkdir(parents=True)
    links_file = links_dir / "SUP_Test_povezane.xlsx"
    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)
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
    new_file = links_file
    manual_new = pd.read_excel(new_file, dtype=str)
    manual_new["sifra_dobavitelja"] = (
        manual_new["sifra_dobavitelja"].fillna("").astype(str)
    )
    assert manual_new["sifra_dobavitelja"].iloc[0] == ""
    manual_new["naziv_ckey"] = manual_new["naziv"].map(_clean)
    lookup = (
        manual_new.set_index(["sifra_dobavitelja", "naziv_ckey"])["wsm_sifra"]
        .to_dict()
    )
    df2 = pd.DataFrame({"sifra_dobavitelja": [pd.NA], "naziv": ["Item"]})
    df2["sifra_dobavitelja"] = df2["sifra_dobavitelja"].fillna("").astype(str)
    df2["naziv_ckey"] = df2["naziv"].map(_clean)
    df2["wsm_sifra"] = df2.apply(
        lambda r: lookup.get((r["sifra_dobavitelja"], r["naziv_ckey"])), axis=1
    )
    assert df2["wsm_sifra"].iloc[0] == "A1"
