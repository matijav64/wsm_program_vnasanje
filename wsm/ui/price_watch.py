# File: wsm/ui/price_watch.py
"""Simple GUI for watching price history of items."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import pandas as pd

from wsm.ui.review_links import _load_supplier_map
from wsm.utils import sanitize_folder_name


def launch_price_watch(suppliers: Path | str = Path("links")) -> None:
    """Launch the price watch window."""
    suppliers = Path(suppliers)
    root = tk.Tk()
    root.title("Spremljanje cen")
    root.geometry("500x400")

    suppliers = _load_supplier_map(suppliers)
    supplier_codes = sorted(suppliers)

    combo_values = [f"{c} - {suppliers[c]['ime']}" for c in supplier_codes]
    if combo_values:
        combo_state = "readonly"
    else:
        combo_values = ["Ni dobaviteljev"]
        combo_state = "disabled"
    combo = ttk.Combobox(root, values=combo_values, width=40, state=combo_state)
    combo.pack(pady=10)

    listbox = tk.Listbox(root, width=60)
    listbox.pack(pady=10, fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(root, width=450, height=150)
    canvas.pack(pady=10)

    item_data: dict[str, pd.DataFrame] = {}

    def on_supplier_selected(event=None):
        sel = combo.get()
        if not sel:
            return
        code = sel.split(" - ")[0]
        name = suppliers.get(code, {}).get("ime", code)
        safe_name = sanitize_folder_name(name)
        hist_path = suppliers / safe_name / "price_history.xlsx"
        listbox.delete(0, tk.END)
        item_data.clear()
        if not hist_path.exists():
            messagebox.showwarning("Opozorilo", "Za izbranega dobavitelja ni podatkov o cenah.")
            return
        df = pd.read_excel(hist_path)
        if "key" not in df.columns:
            return
        for key in sorted(df["key"].unique()):
            listbox.insert(tk.END, key)
            item_data[key] = df[df["key"] == key].sort_values("time")

    def on_item_selected(event=None):
        if not listbox.curselection():
            return
        key = listbox.get(listbox.curselection()[0])
        df_item = item_data.get(key)
        if df_item is None or df_item.empty:
            return
        canvas.delete("all")
        prices = df_item["cena"].astype(float).tolist()
        min_p, max_p = min(prices), max(prices)
        width, height = 450, 150
        margin = 20
        scale = (height - 2 * margin) / (max_p - min_p) if max_p != min_p else 1
        for i in range(1, len(prices)):
            x1 = margin + (i - 1) * (width - 2 * margin) / max(1, len(prices) - 1)
            y1 = height - margin - (prices[i - 1] - min_p) * scale
            x2 = margin + i * (width - 2 * margin) / max(1, len(prices) - 1)
            y2 = height - margin - (prices[i] - min_p) * scale
            canvas.create_line(x1, y1, x2, y2, fill="blue", width=2)
        if len(prices) >= 2:
            last, prev = prices[-1], prices[-2]
            if last > prev:
                arrow = "↑"
            elif last < prev:
                arrow = "↓"
            else:
                arrow = "→"
            canvas.create_text(width - margin, margin, text=arrow, font=("Arial", 16))

    combo.bind("<<ComboboxSelected>>", on_supplier_selected)
    listbox.bind("<<ListboxSelect>>", on_item_selected)

    tk.Button(root, text="Nazaj", command=root.destroy).pack(pady=5)

    root.mainloop()
