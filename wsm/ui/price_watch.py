# File: wsm/ui/price_watch.py
"""Simple GUI for watching price history of items."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path

import pandas as pd
import logging

log = logging.getLogger(__name__)

from wsm.ui.review_links import _load_supplier_map
from wsm.utils import sanitize_folder_name


def launch_price_watch(suppliers: Path | str | None = None) -> None:
    """Launch the price watch window.

    When ``suppliers`` is ``None`` it will read the path from the
    ``WSM_SUPPLIERS`` environment variable and fall back to ``links`` in the
    current working directory.
    """
    suppliers_dir = Path(suppliers or os.getenv("WSM_SUPPLIERS", "links"))
    if not suppliers_dir.exists():
        log.info(
            "Supplier path %s does not exist (WSM_SUPPLIERS=%s)",
            suppliers_dir,
            os.getenv("WSM_SUPPLIERS"),
        )
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Napaka", f"Mapa dobaviteljev ni najdena: {suppliers_dir}"
        )
        root.destroy()
        return

    root = tk.Tk()
    root.title("Spremljanje cen")
    root.geometry("500x400")

    suppliers_map = _load_supplier_map(suppliers_dir)

    # Preberemo price_history.xlsx po posameznih dobaviteljih
    items_by_supplier: dict[str, dict[str, pd.DataFrame]] = {}
    for code, info in suppliers_map.items():
        safe_name = sanitize_folder_name(info.get("ime", code))
        hist_path = suppliers_dir / safe_name / "price_history.xlsx"
        if not hist_path.exists():
            continue
        df = pd.read_excel(hist_path)
        if "key" not in df.columns:
            continue
        if "code" not in df.columns or "name" not in df.columns:
            parts = df["key"].str.split("_", n=1, expand=True)
            if "code" not in df.columns:
                df["code"] = parts[0]
            if "name" not in df.columns:
                df["name"] = parts[1].fillna("")
        df["label"] = df["code"].astype(str) + " - " + df["name"].astype(str)
        for label in df["label"].unique():
            sub = df[df["label"] == label].sort_values("time")
            items_by_supplier.setdefault(code, {})[label] = sub

    supplier_var = tk.StringVar()
    supplier_codes = {
        f"{code} - {info['ime']}": code for code, info in suppliers_map.items()
    }
    supplier_box = ttk.Combobox(
        root, values=list(supplier_codes), textvariable=supplier_var, state="readonly", width=47
    )
    supplier_box.pack(pady=5)

    if supplier_codes:
        supplier_box.current(0)
        # populate initial item list
        supplier_var.set(list(supplier_codes)[0])

    search_var = tk.StringVar()
    entry = ttk.Entry(root, textvariable=search_var, width=45)
    entry.pack(pady=5)

    listbox = tk.Listbox(root, width=60)
    listbox.pack(pady=10, fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(root, width=450, height=150)
    canvas.pack(pady=10)
    info_label = tk.Label(root, text="")
    info_label.pack()

    def update_list(event=None):
        code = supplier_codes.get(supplier_var.get())
        query = search_var.get().lower()
        listbox.delete(0, tk.END)
        if not code:
            return
        for key in sorted(items_by_supplier.get(code, {})):
            if query in key.lower():
                listbox.insert(tk.END, key)

    def on_item_selected(event=None):
        if not listbox.curselection():
            return
        key = listbox.get(listbox.curselection()[0])
        code = supplier_codes.get(supplier_var.get())
        if not code:
            return
        df_item = items_by_supplier.get(code, {}).get(key)
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
        last_time = pd.to_datetime(df_item["time"].iloc[-1])
        info_label.config(text=f"Zadnja cena: {prices[-1]} (\u010das: {last_time:%Y-%m-%d})")

    entry.bind("<KeyRelease>", update_list)
    supplier_box.bind("<<ComboboxSelected>>", update_list)
    listbox.bind("<<ListboxSelect>>", on_item_selected)

    update_list()

    tk.Button(root, text="Nazaj", command=root.destroy).pack(pady=5)

    root.mainloop()
