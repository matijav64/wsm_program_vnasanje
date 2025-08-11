from decimal import Decimal
import inspect
import textwrap
from types import SimpleNamespace

import pandas as pd
import pytest

import wsm.ui.review.gui as rl
from wsm.ui.review.io import _save_and_close
from wsm.utils import _clean

pytest.importorskip("openpyxl")


class DummyRoot:
    def quit(self):
        pass


def _extract_multiplier_prompt():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(
        i
        for i, line in enumerate(src)
        if line.strip().startswith("def _apply_multiplier_prompt")
    )
    end = next(
        i
        for i, line in enumerate(src[start + 1 :], start + 1)  # noqa: E203
        if line.strip().startswith("def ")
    )
    snippet = textwrap.dedent("\n".join(src[start:end]))

    class DummyButton:
        def __init__(self, *a, **k):
            pass

        def grid(self, *a, **k):
            pass

    ns = {
        "Decimal": Decimal,
        "_apply_multiplier": rl._apply_multiplier,
        "simpledialog": SimpleNamespace(askinteger=lambda *a, **k: 10),
        "tk": SimpleNamespace(Button=DummyButton),
        "btn_frame": None,
    }
    exec(snippet, ns)
    return ns["_apply_multiplier_prompt"], ns


def test_quantity_multiplier_persist(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["1"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kos"],
            "vrednost": [Decimal("10")],
            "rabata": [Decimal("0")],
            "ddv": [Decimal("0")],
            "ddv_stopnja": [Decimal("0")],
            "sifra_artikla": [pd.NA],
        }
    )
    df["wsm_sifra"] = ["A1"]
    df["dobavitelj"] = ["Test"]
    df["kolicina_norm"] = df["kolicina"]
    df["enota_norm"] = ["kos"]
    df["cena_pred_rabatom"] = df["vrednost"] / df["kolicina"]
    df["cena_po_rabatu"] = df["vrednost"] / df["kolicina"]
    df["total_net"] = df["vrednost"]
    df["naziv_ckey"] = df["naziv"].map(_clean)
    df["multiplier"] = Decimal("1")

    original_total = df.at[0, "total_net"]
    rl._apply_multiplier(df, 0, Decimal("10"))
    assert df.at[0, "kolicina_norm"] == Decimal("10")
    assert df.at[0, "cena_po_rabatu"] == Decimal("1")
    assert df.at[0, "total_net"] == original_total

    manual_old = pd.DataFrame(
        columns=[
            "sifra_dobavitelja",
            "naziv",
            "wsm_sifra",
            "dobavitelj",
            "naziv_ckey",
            "enota_norm",
            "multiplier",
        ]
    )
    wsm_df = pd.DataFrame({"wsm_sifra": ["A1"], "wsm_naziv": ["Art"]})
    base_dir = tmp_path / "suppliers"
    links_dir = base_dir / "Test"
    links_dir.mkdir(parents=True)
    links_file = links_dir / "SUP_Test_povezane.xlsx"

    monkeypatch.setattr("wsm.utils.log_price_history", lambda *a, **k: None)
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)
    monkeypatch.setattr(
        "wsm.ui.review.io._write_history_files", lambda *a, **k: False
    )

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

    saved_file = base_dir / "SUP" / "SUP_povezane.xlsx"
    manual_new = pd.read_excel(saved_file)
    assert manual_new.loc[0, "multiplier"] == 10

    captured = {}
    original_apply = rl._apply_multiplier

    def capture_apply(dframe, idx, mult, *args, **kwargs):
        original_apply(dframe, idx, mult, *args, **kwargs)
        captured["df"] = dframe.copy()

    monkeypatch.setattr(rl, "_apply_multiplier", capture_apply)
    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr(rl, "_build_header_totals", lambda *a, **k: {})
    monkeypatch.setattr(
        rl.tk, "Tk", lambda: (_ for _ in ()).throw(RuntimeError)
    )

    with pytest.raises(RuntimeError):
        rl.review_links(
            pd.DataFrame(
                {
                    "sifra_dobavitelja": ["1"],
                    "naziv": ["Item"],
                    "kolicina": [Decimal("1")],
                    "enota": ["kos"],
                    "vrednost": [Decimal("10")],
                    "rabata": [Decimal("0")],
                    "ddv": [Decimal("0")],
                    "ddv_stopnja": [Decimal("0")],
                    "sifra_artikla": [pd.NA],
                }
            ),
            pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"]),
            saved_file,
            Decimal("10"),
        )

    reloaded = captured["df"]
    assert reloaded.at[0, "kolicina_norm"] == Decimal("10")
    assert reloaded.at[0, "cena_po_rabatu"] == Decimal("1")
    assert reloaded.at[0, "total_net"] == Decimal("10")


def test_multiplier_button(monkeypatch):
    apply_prompt, ns = _extract_multiplier_prompt()

    df = pd.DataFrame(
        {
            "kolicina_norm": [Decimal("1")],
            "cena_pred_rabatom": [Decimal("10")],
            "cena_po_rabatu": [Decimal("10")],
            "total_net": [Decimal("10")],
            "multiplier": [Decimal("1")],
        }
    )

    class DummyTree:
        def focus(self):
            return "0"

        def set(self, *args, **kwargs):
            pass

    called = {"summary": 0, "totals": 0}
    ns.update(
        {
            "df": df,
            "tree": DummyTree(),
            "_update_summary": lambda: called.__setitem__(
                "summary", called["summary"] + 1
            ),
            "_update_totals": lambda: called.__setitem__(
                "totals", called["totals"] + 1
            ),
            "root": object(),
        }
    )

    apply_prompt()

    assert df.at[0, "kolicina_norm"] == Decimal("10")
    assert df.at[0, "cena_pred_rabatom"] == Decimal("1")
    assert df.at[0, "cena_po_rabatu"] == Decimal("1")
    assert df.at[0, "total_net"] == Decimal("10")
    assert df.at[0, "multiplier"] == Decimal("10")
    assert called["summary"] == 1
    assert called["totals"] == 1
