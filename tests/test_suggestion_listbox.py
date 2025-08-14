import inspect
import textwrap
from decimal import Decimal
import shutil

import pandas as pd
import tkinter as tk
from tkinter import ttk
from pyvirtualdisplay import Display
import pytest

if shutil.which("Xvfb") is None:
    pytest.skip("Xvfb not installed", allow_module_level=True)

import wsm.ui.review.gui as rl


def _extract_confirm():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, line in enumerate(src) if "def _confirm" in line)
    end = next(
        i
        for i, line in enumerate(src[start:], start)
        if line.strip().startswith("def _apply_multiplier_prompt")
    )
    snippet = textwrap.dedent("\n".join(src[start:end]))
    ns = {
        "pd": pd,
        "Decimal": Decimal,
        "_apply_price_warning": lambda *a, **k: (False, ""),
        "_show_tooltip": lambda *a, **k: None,
        "_fmt": rl._fmt,
        "log": rl.log,
        "price_warn_threshold": Decimal("5"),
        "_schedule_totals": lambda: None,
        "_close_suggestions": lambda: None,
    }
    exec(snippet, ns)
    return ns["_confirm"], ns


def test_listbox_hidden_after_confirm(monkeypatch, tmp_path):
    _confirm, ns = _extract_confirm()
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["1"],
            "naziv": ["Item"],
            "kolicina_norm": [Decimal("1")],
            "enota_norm": ["kos"],
            "rabata_pct": [Decimal("0")],
            "cena_pred_rabatom": [Decimal("1")],
            "cena_po_rabatu": [Decimal("1")],
            "total_net": [Decimal("1")],
            "warning": [""],
            "wsm_naziv": [pd.NA],
            "dobavitelj": [pd.NA],
        }
    )

    with Display():
        root = tk.Tk()
        root.withdraw()

        entry = ttk.Entry(root)
        entry.grid(row=0, column=0)

        root.deiconify()
        lb = tk.Listbox(root)
        lb.grid(row=1, column=0)
        lb.insert(0, "Foo")
        lb.selection_set(0)

        cols = [
            "naziv",
            "kolicina_norm",
            "enota_norm",
            "rabata_pct",
            "cena_pred_rabatom",
            "cena_po_rabatu",
            "total_net",
            "warning",
            "wsm_naziv",
            "dobavitelj",
        ]
        tree = ttk.Treeview(root, columns=cols, show="headings")
        tree.grid(row=2, column=0)
        tree.insert("", "end", iid="0", values=["" for _ in cols])
        tree.focus("0")

        focused = {"value": False}
        orig_focus_set = tree.focus_set

        def track_focus():
            focused["value"] = True
            orig_focus_set()

        tree.focus_set = track_focus

        ns.update(
            {
                "df": df,
                "tree": tree,
                "entry": entry,
                "lb": lb,
                "n2s": {"Foo": "X"},
                "supplier_name": "Test",
                "suppliers_file": tmp_path,
                "cols": cols,
                "_update_summary": lambda: None,
                "_schedule_totals": lambda: None,
            }
        )
        cleared = {"value": False}

        def close_suggestions():
            lb.grid_remove()
            lb.selection_clear(0, "end")
            cleared["value"] = True
            entry.focus_set()

        ns["_close_suggestions"] = close_suggestions
        monkeypatch.setattr("wsm.utils.load_last_price", lambda *a, **k: None)

        root.update()
        _confirm()
        root.update()

        assert not lb.winfo_ismapped()
        assert cleared["value"]
        assert focused["value"]
        root.destroy()
