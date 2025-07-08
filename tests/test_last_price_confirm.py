import pytest
pytest.importorskip("openpyxl")
from decimal import Decimal
import pandas as pd
import inspect
import textwrap

import wsm.ui.review as rl
from wsm.utils import last_price_stats, load_last_price


def test_last_price_stats_order_independent():
    df = pd.DataFrame({
        "cena": [Decimal("1"), Decimal("2"), Decimal("1.5")],
        "time": [
            pd.Timestamp("2023-01-03"),
            pd.Timestamp("2023-01-01"),
            pd.Timestamp("2023-01-02"),
        ],
    })
    stats = last_price_stats(df)
    assert stats == {
        "last_price": Decimal("1"),
        "last_dt": pd.Timestamp("2023-01-03"),
        "min": Decimal("1"),
        "max": Decimal("2"),
    }


def test_load_last_price_single_supplier(tmp_path):
    links = tmp_path / "links"
    sup = links / "SUP"
    sup.mkdir(parents=True)
    df = pd.DataFrame({
        "key": ["A_Item", "A_Item"],
        "code": ["A", "A"],
        "name": ["Item", "Item"],
        "line_netto": [1, 2],
        "unit_price": [pd.NA, pd.NA],
        "time": [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-02-01")],
    })
    df.to_excel(sup / "price_history.xlsx", index=False)
    price = load_last_price("A - Item", links)
    assert price == Decimal("2")


class DummyTree:
    def __init__(self):
        self.tags = {}
        self.values = {}
        self.focus_id = "0"

    def focus(self):
        return self.focus_id

    def next(self, _):
        return None

    def selection_set(self, _):
        pass

    def focus_set(self):
        pass

    def see(self, _):
        pass

    def item(self, iid, **kw):
        if "tags" in kw:
            self.tags[iid] = kw["tags"]
        if "values" in kw:
            self.values[iid] = kw["values"]
        return {"tags": self.tags.get(iid, ()), "values": self.values.get(iid)}


class DummyEntry:
    def __init__(self, value=""):
        self.val = value

    def get(self):
        return self.val

    def delete(self, *a):
        pass


class DummyListbox:
    def __init__(self):
        self.items = []

    def curselection(self):
        return []

    def get(self, i):
        return self.items[i]

    def pack_forget(self):
        pass


def _extract_confirm(threshold=Decimal("5")):
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, l in enumerate(src) if "def _confirm" in l)
    end = next(i for i, l in enumerate(src[start:], start) if l.strip().startswith("def _clear"))
    snippet = textwrap.dedent("\n".join(src[start:end]))
    ns = {
        "pd": pd,
        "Decimal": Decimal,
        "_apply_price_warning": rl._apply_price_warning,
        "_show_tooltip": lambda *a, **k: None,
        "_fmt": rl._fmt,
        "log": rl.log,
        "price_warn_threshold": threshold,
    }
    exec(snippet, ns)
    return ns["_confirm"], ns


def test_confirm_applies_price_warning(monkeypatch, tmp_path):
    _confirm, ns = _extract_confirm()
    df = pd.DataFrame(
        {
            "naziv": ["Item"],
            "sifra_dobavitelja": ["SUP"],
            "cena_po_rabatu": [Decimal("11")],
            "kolicina_norm": [1],
            "enota_norm": ["kg"],
            "rabata_pct": [0],
            "cena_pred_rabatom": [10],
            "total_net": [11],
            "wsm_naziv": [pd.NA],
            "dobavitelj": [pd.NA],
            "wsm_sifra": [pd.NA],
            "status": [pd.NA],
        }
    )
    ns.update(
        {
            "tree": DummyTree(),
            "entry": DummyEntry("X"),
            "lb": DummyListbox(),
            "df": df,
            "n2s": {"X": "X1"},
            "supplier_name": "Test",
            "suppliers_file": tmp_path,
            "cols": [
                "naziv",
                "kolicina_norm",
                "enota_norm",
                "rabata_pct",
                "cena_pred_rabatom",
                "cena_po_rabatu",
                "total_net",
                "wsm_naziv",
                "dobavitelj",
            ],
            "_update_summary": lambda: None,
            "_update_totals": lambda: None,
        }
    )
    monkeypatch.setattr("wsm.utils.load_last_price", lambda *a, **k: Decimal("10"))
    _confirm()
    assert ns["tree"].tags.get("0") == ("price_warn",)


def test_confirm_respects_threshold(monkeypatch, tmp_path):
    _confirm, ns = _extract_confirm(threshold=Decimal("20"))
    df = pd.DataFrame(
        {
            "naziv": ["Item"],
            "sifra_dobavitelja": ["SUP"],
            "cena_po_rabatu": [Decimal("11")],
            "kolicina_norm": [1],
            "enota_norm": ["kg"],
            "rabata_pct": [0],
            "cena_pred_rabatom": [10],
            "total_net": [11],
            "wsm_naziv": [pd.NA],
            "dobavitelj": [pd.NA],
            "wsm_sifra": [pd.NA],
            "status": [pd.NA],
        }
    )
    ns.update(
        {
            "tree": DummyTree(),
            "entry": DummyEntry("X"),
            "lb": DummyListbox(),
            "df": df,
            "n2s": {"X": "X1"},
            "supplier_name": "Test",
            "suppliers_file": tmp_path,
            "cols": [
                "naziv",
                "kolicina_norm",
                "enota_norm",
                "rabata_pct",
                "cena_pred_rabatom",
                "cena_po_rabatu",
                "total_net",
                "wsm_naziv",
                "dobavitelj",
            ],
            "_update_summary": lambda: None,
            "_update_totals": lambda: None,
        }
    )
    monkeypatch.setattr("wsm.utils.load_last_price", lambda *a, **k: Decimal("10"))
    _confirm()
    assert ns["tree"].tags.get("0") == ()

