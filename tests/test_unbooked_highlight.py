import pandas as pd
from decimal import Decimal
import tkinter as tk
from tkinter import ttk

from wsm.utils import _clean
from wsm.ui.review.helpers import _fmt
from pyvirtualdisplay import Display


def test_unbooked_highlight():
    df = pd.DataFrame([
        {
            "sifra_dobavitelja": "1",
            "naziv": "Booked",
            "enota_norm": "",
            "kolicina_norm": Decimal("1"),
            "cena_pred_rabatom": Decimal("0"),
            "rabata_pct": Decimal("0"),
            "cena_po_rabatu": Decimal("0"),
            "total_net": Decimal("0"),
            "warning": "",
            "wsm_naziv": "",
            "dobavitelj": "",
            "multiplier": Decimal("1"),
        },
        {
            "sifra_dobavitelja": "2",
            "naziv": "Unbooked",
            "enota_norm": "",
            "kolicina_norm": Decimal("1"),
            "cena_pred_rabatom": Decimal("0"),
            "rabata_pct": Decimal("0"),
            "cena_po_rabatu": Decimal("0"),
            "total_net": Decimal("0"),
            "warning": "",
            "wsm_naziv": "",
            "dobavitelj": "",
            "multiplier": Decimal("1"),
        },
        {
            "sifra_dobavitelja": "3",
            "naziv": "Multiplied",
            "enota_norm": "",
            "kolicina_norm": Decimal("1"),
            "cena_pred_rabatom": Decimal("0"),
            "rabata_pct": Decimal("0"),
            "cena_po_rabatu": Decimal("0"),
            "total_net": Decimal("0"),
            "warning": "",
            "wsm_naziv": "",
            "dobavitelj": "",
            "multiplier": Decimal("10"),
        },
    ])
    df["naziv_ckey"] = df["naziv"].map(_clean)

    manual_old = pd.DataFrame([
        {
            "sifra_dobavitelja": "1",
            "naziv": "Booked",
            "wsm_sifra": "WS1",
        }
    ])
    manual_old["naziv_ckey"] = manual_old["naziv"].map(_clean)

    booked_keys = {
        (str(s), ck)
        for s, ck, ws in manual_old[["sifra_dobavitelja", "naziv_ckey", "wsm_sifra"]].itertuples(index=False)
        if pd.notna(ws) and str(ws).strip()
    }

    with Display():
        root = tk.Tk()
        root.withdraw()

        cols = [
            "sifra_dobavitelja",
            "naziv",
            "enota_norm",
            "kolicina_norm",
            "cena_pred_rabatom",
            "rabata_pct",
            "cena_po_rabatu",
            "total_net",
            "warning",
            "wsm_naziv",
            "dobavitelj",
        ]
        tree = ttk.Treeview(root, columns=cols, show="headings", height=10)
        tree.tag_configure("unbooked", background="lightpink")

        for i, row in df.iterrows():
            vals = [
                (
                    _fmt(row[c])
                    if isinstance(row[c], (Decimal, float, int))
                    else ("" if pd.isna(row[c]) else str(row[c]))
                )
                for c in cols
            ]
            tree.insert("", "end", iid=str(i), values=vals)
            tree.item(str(i), tags=())
            key = (str(row["sifra_dobavitelja"]), row["naziv_ckey"])
            if key not in booked_keys:
                multiplier = row.get("multiplier", Decimal("1"))
                if multiplier <= 1:
                    current_tags = tree.item(str(i)).get("tags", ())
                    if not isinstance(current_tags, tuple):
                        current_tags = (current_tags,) if current_tags else ()
                    tree.item(str(i), tags=current_tags + ("unbooked",))

        assert "unbooked" not in tree.item("0").get("tags", ())
        assert "unbooked" in tree.item("1").get("tags", ())
        assert "unbooked" not in tree.item("2").get("tags", ())

        root.destroy()
