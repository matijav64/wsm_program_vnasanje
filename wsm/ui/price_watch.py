# File: wsm/ui/price_watch.py
"""GUI for browsing price history of supplier items."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import pandas as pd
import logging

log = logging.getLogger(__name__)

from wsm.supplier_store import load_suppliers as _load_supplier_map
from wsm.utils import sanitize_folder_name
from functools import lru_cache


@lru_cache(maxsize=None)
def _load_price_histories(suppliers_dir: Path | str) -> dict[str, dict[str, pd.DataFrame]]:
    """Return price history grouped by supplier and item label."""

    suppliers_dir = Path(suppliers_dir).resolve()
    suppliers_map = _load_supplier_map(suppliers_dir)
    items_by_supplier: dict[str, dict[str, pd.DataFrame]] = {}
    for code, info in suppliers_map.items():
        safe_id = sanitize_folder_name(info.get("vat") or info.get("ime", code))
        hist_path = suppliers_dir / safe_id / "price_history.xlsx"
        log.debug("Checking history file for %s at %s", code, hist_path)
        if not hist_path.exists():
            log.info("price_history.xlsx ni najden: %s", hist_path)
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
        if "line_netto" not in df.columns and "cena" in df.columns:
            df.rename(columns={"cena": "line_netto"}, inplace=True)
        if "unit_price" not in df.columns:
            df["unit_price"] = pd.NA
        if "enota_norm" not in df.columns:
            df["enota_norm"] = pd.NA
        df["line_netto"] = pd.to_numeric(df.get("line_netto"), errors="coerce")
        df["unit_price"] = pd.to_numeric(df.get("unit_price"), errors="coerce")
        df["cena"] = df["unit_price"].fillna(df["line_netto"])

        # Convert time to datetime and drop rows that fail conversion
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            df = df.dropna(subset=["time"])

        df["label"] = df["code"].astype(str) + " - " + df["name"].astype(str)
        for label in df["label"].unique():
            sub = df[df["label"] == label].sort_values("time")
            items_by_supplier.setdefault(code, {})[label] = sub
    return items_by_supplier


def clear_price_cache() -> None:
    """Clear cached price histories."""
    _load_price_histories.cache_clear()


class PriceWatch(tk.Toplevel):
    """Window for browsing historic prices."""

    def __init__(self, master: tk.Misc | None = None, suppliers: Path | str | None = None) -> None:
        super().__init__(master)
        self.title("Spremljanje cen")
        self.geometry("600x400")

        self.suppliers_dir = Path(suppliers or os.getenv("WSM_SUPPLIERS", "links"))
        if not self.suppliers_dir.exists():
            self.withdraw()
            messagebox.showerror(
                "Napaka", f"Mapa dobaviteljev ni najdena: {self.suppliers_dir}"
            )
            self.destroy()
            return

        self.suppliers_map = _load_supplier_map(self.suppliers_dir)
        self.items_by_supplier = _load_price_histories(self.suppliers_dir)
        self.supplier_codes = {
            f"{code} - {info['ime']}": code for code, info in self.suppliers_map.items()
        }

        self._sort_col: str | None = None
        self._sort_reverse = False

        self._build_supplier_search()
        self._build_article_table()
        self._build_back_button()

        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh_table()

    # ------------------------------------------------------------------
    def _build_supplier_search(self) -> None:
        frame = tk.Frame(self)
        frame.pack(pady=5, fill=tk.X)

        self.sup_search_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.sup_search_var, width=20)
        entry.pack(side=tk.LEFT, padx=5)

        self.sup_var = tk.StringVar()
        self.sup_box = ttk.Combobox(frame, textvariable=self.sup_var, state="readonly", width=45)
        self.sup_box.pack(side=tk.LEFT, padx=5)

        self._supplier_names = list(self.supplier_codes)
        self._update_supplier_list()

        entry.bind("<KeyRelease>", lambda e: self._update_supplier_list())
        self.sup_box.bind("<<ComboboxSelected>>", lambda e: self._refresh_table())

    def _build_article_table(self) -> None:
        self.search_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.search_var)
        entry.pack(pady=5, fill=tk.X)
        entry.bind("<KeyRelease>", lambda e: self._refresh_table())

        columns = ("label", "line_netto", "unit_price", "last_dt", "min", "max")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        headings = {
            "label": "Artikel",
            "line_netto": "Neto cena",
            "unit_price": "€/kg|€/L",
            "last_dt": "Zadnji datum",
            "min": "Min",
            "max": "Max",
        }
        numeric_cols = {"line_netto", "unit_price", "min", "max"}
        for col in columns:
            self.tree.heading(col, text=headings[col], command=lambda c=col: self._sort_by(c))
            width = 220 if col == "label" else 90
            anchor = tk.W if col == "label" else tk.E
            fmt = "%.2f" if col in numeric_cols else ""
            self.tree.column(col, width=width, anchor=anchor, format=fmt)

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<BackSpace>", lambda e: self.destroy())

    def _build_back_button(self) -> None:
        ttk.Button(self, text="Nazaj", command=self.destroy).pack(pady=5)

    # ------------------------------------------------------------------
    def _update_supplier_list(self) -> None:
        query = self.sup_search_var.get().lower()
        opts = [s for s in self._supplier_names if query in s.lower()]
        self.sup_box["values"] = opts
        if opts:
            if self.sup_var.get() not in opts:
                self.sup_box.current(0)
                self.sup_var.set(opts[0])
        else:
            self.sup_var.set("")
        self._refresh_table()

    def _refresh_table(self) -> None:
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        code = self.supplier_codes.get(self.sup_var.get())
        if not code:
            return
        items = self.items_by_supplier.get(code, {})
        rows: list[dict] = []
        query = self.search_var.get().lower()
        for label, df in items.items():
            if query and query not in label.lower():
                continue

            line_prices = pd.to_numeric(df.get("line_netto"), errors="coerce")
            unit_prices = pd.to_numeric(df.get("unit_price"), errors="coerce")

            last_line = line_prices.dropna()
            last_unit = unit_prices.dropna()

            stats_series = unit_prices.dropna()
            if stats_series.empty:
                stats_series = line_prices.dropna()

            if stats_series.empty:
                continue

            last_idx = stats_series.index[-1]

            rows.append(
                {
                    "label": label,
                    "line_netto": float(last_line.iloc[-1]) if not last_line.empty else None,
                    "unit_price": float(last_unit.iloc[-1]) if not last_unit.empty else None,
                    "last_dt": pd.to_datetime(df.loc[last_idx, "time"]),
                    "min": float(stats_series.min()),
                    "max": float(stats_series.max()),
                    "df": df,
                }
            )
        if not rows:
            messagebox.showinfo("Ni podatkov", "Ni zadetkov za izbrane filtre.")
            return
        if self._sort_col:
            rows.sort(
                key=lambda r: (r[self._sort_col] is None, r[self._sort_col]),
                reverse=self._sort_reverse,
            )
        for r in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    r["label"],
                    "" if r["line_netto"] is None else f"{r['line_netto']}",
                    "" if r["unit_price"] is None else f"{r['unit_price']}",
                    r["last_dt"].strftime("%Y-%m-%d"),
                    f"{r['min']}",
                    f"{r['max']}",
                ),
            )

    def _sort_by(self, column: str) -> None:
        if self._sort_col == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = column
            self._sort_reverse = False
        self._refresh_table()

    def _on_double_click(self, event: tk.Event | None = None) -> None:
        item_id = self.tree.focus()
        if not item_id:
            return
        label = self.tree.item(item_id)["values"][0]
        code = self.supplier_codes.get(self.sup_var.get())
        df_item = self.items_by_supplier.get(code, {}).get(label)
        if df_item is not None and not df_item.empty:
            self._show_graph(label, df_item)

    def _show_graph(self, label: str, df: pd.DataFrame) -> None:
        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as exc:  # pragma: no cover - optional dependency
            messagebox.showerror("Napaka", f"Matplotlib ni na voljo: {exc}")
            return

        top = tk.Toplevel(self)
        top.title(label)

        fig, ax = plt.subplots(figsize=(5, 3))
        unit_series = pd.to_numeric(df.get("unit_price"), errors="coerce")
        if unit_series.notna().any():
            price_series = unit_series
        else:
            price_series = pd.to_numeric(df.get("line_netto"), errors="coerce")
        ax.plot(pd.to_datetime(df["time"]), price_series, marker="o")
        # Ensure each timestamp appears on the x-axis for clarity
        ax.set_xticks(pd.to_datetime(df["time"]))
        fig.autofmt_xdate()
        ax.set_xlabel("Datum")
        ax.set_ylabel("Cena")
        ax.grid(True)
        # Add a little horizontal padding so points at the edges are visible
        ax.margins(x=0.05)

        canvas = FigureCanvasTkAgg(fig, master=top)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        ttk.Button(top, text="Zapri", command=top.destroy).pack(pady=5)
        top.bind("<Escape>", lambda e: top.destroy())


def launch_price_watch(master: tk.Misc, suppliers: Path | str | None = None) -> None:
    """Launch the price watch window."""

    PriceWatch(master, suppliers).mainloop()

