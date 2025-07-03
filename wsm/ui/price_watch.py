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
from typing import Any


def _color_for_diff(pct: float) -> str:
    """Return a hex color from blue (negative) to red (positive)."""
    if pct is None:
        return ""
    pct = max(min(pct, 100), -100)
    frac = abs(pct) / 100
    base = int(255 * (1 - frac))
    if pct >= 0:
        r, g, b = 255, base, base
    else:
        r, g, b = base, base, 255
    return f"#{r:02x}{g:02x}{b:02x}"



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

        # Use service_date when available for the timeline
        if "service_date" in df.columns:
            df["service_date"] = pd.to_datetime(df["service_date"], errors="coerce")
            df["time"] = df["service_date"].combine_first(pd.to_datetime(df.get("time"), errors="coerce"))
        elif "time" in df.columns:
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
            self._close()
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

        self.bind("<Escape>", lambda e: self._close())
        self._refresh_table()

    # ------------------------------------------------------------------
    def _close(self) -> None:
        """Destroy the window and quit its event loop."""
        try:
            self.destroy()
        finally:
            if getattr(self, "quit", None):
                self.quit()

    # ------------------------------------------------------------------
    def _build_supplier_search(self) -> None:
        frame = tk.Frame(self)
        frame.pack(pady=5, fill=tk.X)

        self.sup_search_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.sup_search_var, width=20)
        entry.pack(side=tk.LEFT, padx=5)

        # Interval (weeks) for price change calculation
        ttk.Label(frame, text="Tedni:").pack(side=tk.LEFT, padx=(10, 2))
        self.weeks_var = tk.StringVar(value="2")
        spin = ttk.Spinbox(frame, from_=1, to=520, textvariable=self.weeks_var, width=5)
        spin.pack(side=tk.LEFT, padx=5)
        spin.configure(command=self._refresh_table)
        spin.bind("<KeyRelease>", lambda e: self._refresh_table())
        ttk.Button(frame, text="Potrdi", command=self._refresh_table).pack(side=tk.LEFT, padx=5)

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

        columns = (
            "label",
            "line_netto",
            "unit_price",
            "last_dt",
            "diff_pct",
            "min",
            "max",
        )
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        headings = {
            "label": "Artikel",
            "line_netto": "Neto cena",
            "unit_price": "€/kg|€/L",
            "last_dt": "Zadnji datum",
            "diff_pct": "% diff",
            "min": "Min",
            "max": "Max",
        }
        for col in columns:
            self.tree.heading(col, text=headings[col], command=lambda c=col: self._sort_by(c))
            width = 220 if col == "label" else 90
            anchor = tk.W if col == "label" else tk.E
            self.tree.column(col, width=width, anchor=anchor)

        # Tag for highlighting notable price changes
        self.tree.tag_configure("chg", background="#ffcccc")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<BackSpace>", lambda e: self._close())

    def _build_back_button(self) -> None:
        ttk.Button(self, text="Nazaj", command=self._close).pack(pady=5)

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
        tree = getattr(self, "tree", None)
        if not tree:
            return
        if hasattr(tree, "winfo_exists") and not tree.winfo_exists():
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

            line_prices_full = pd.to_numeric(df.get("line_netto"), errors="coerce")
            unit_prices_full = pd.to_numeric(df.get("unit_price"), errors="coerce")

            last_line = line_prices_full.dropna()
            last_unit = unit_prices_full.dropna()

            diff_series_full = unit_prices_full.dropna()
            if diff_series_full.empty:
                diff_series_full = line_prices_full.dropna()
            if diff_series_full.empty:
                continue

            last_idx = diff_series_full.index[-1]

            weeks = 0
            if hasattr(self, "weeks_var"):
                try:
                    weeks = int(self.weeks_var.get())
                except Exception:
                    weeks = 0
            if weeks:
                cutoff = pd.Timestamp.now() - pd.Timedelta(weeks=weeks)
                df_recent = df[df["time"] >= cutoff]
            else:
                df_recent = df

            line_eval = line_prices_full.loc[df_recent.index]
            unit_eval = unit_prices_full.loc[df_recent.index]

            diff_series_eval = unit_eval.dropna()
            if diff_series_eval.empty:
                diff_series_eval = line_eval.dropna()

            stats_series_eval = unit_eval.dropna()

            diff_pct: float | None
            if len(diff_series_eval) >= 2:
                first_val = diff_series_eval.iloc[0]
                last_val = diff_series_eval.iloc[-1]
                if first_val != 0 and pd.notna(first_val) and pd.notna(last_val):
                    diff_pct = float((last_val - first_val) / first_val * 100)
                else:
                    diff_pct = None
            elif len(diff_series_eval) == 1:
                diff_pct = 0.0
            else:
                diff_pct = None

            if stats_series_eval.empty:
                min_val = None
                max_val = None
            else:
                min_val = float(stats_series_eval.min())
                max_val = float(stats_series_eval.max())

            rows.append(
                {
                    "label": label,
                    "line_netto": float(last_line.iloc[-1]) if not last_line.empty else None,
                    "unit_price": float(last_unit.iloc[-1]) if not last_unit.empty else None,
                    "last_dt": pd.to_datetime(df.loc[last_idx, "time"]),
                    "diff_pct": diff_pct,
                    "min": min_val,
                    "max": max_val,
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
        for idx, r in enumerate(rows):
            tag = None
            if r.get("diff_pct") is not None:
                color = _color_for_diff(r["diff_pct"])
                tag = f"diff_{idx}"
                cfg = getattr(self.tree, "tag_configure", None)
                if callable(cfg):
                    try:
                        cfg(tag, background=color)
                    except Exception:  # pragma: no cover - ignore test dummies
                        pass
            vals = (
                r["label"],
                "" if r["line_netto"] is None else f"{r['line_netto']:.2f}",
                "" if r["unit_price"] is None else f"{r['unit_price']:.2f}",
                r["last_dt"].strftime("%Y-%m-%d"),
                "—" if r["diff_pct"] is None else f"{r['diff_pct']:.2f}",
                "—" if r["min"] is None else f"{r['min']:.2f}",
                "—" if r["max"] is None else f"{r['max']:.2f}",
            )
            kwargs = {"tags": (tag,)} if tag else {}
            try:
                self.tree.insert("", "end", values=vals, **kwargs)
            except TypeError:  # pragma: no cover - for test dummies
                self.tree.insert("", "end", values=vals)

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
            import matplotlib.dates as mdates
            from matplotlib.ticker import FuncFormatter
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            import mplcursors
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

        dates = pd.to_datetime(df["time"]).dt.normalize()
        mask = price_series.ne(0)
        weeks = 0
        if hasattr(self, "weeks_var"):
            try:
                weeks = int(self.weeks_var.get())
            except Exception:
                weeks = 0
        if weeks:
            cutoff = pd.Timestamp.now() - pd.Timedelta(weeks=weeks)
            mask &= dates >= cutoff
        df_plot = pd.DataFrame({"date": dates[mask], "price": price_series[mask]})
        if df_plot.empty:
            mask = price_series.ne(0)
            df_plot = pd.DataFrame({"date": dates[mask], "price": price_series[mask]})

        # ── 1) grobo zaokroževanje, da odpravimo nenamerne drobne odklone ──
        df_plot["_price_round"] = df_plot["price"].round(2)

        # ── 2) eno vrstico na dan (če so v istem dnevu še vedno razlike, vzemi srednjo) ──
        grp = (
            df_plot
            .groupby(df_plot["date"].dt.date)["_price_round"]
            .mean()
            .round(2)
        )

        ax.plot(list(grp.index), grp.values, marker="o", linestyle="-")
        cursor = mplcursors.cursor(ax.get_lines(), hover=True)

        def _fmt(sel: Any) -> None:
            try:
                dt = mdates.num2date(sel.target[0])
            except Exception:
                dt = pd.to_datetime(sel.target[0])
            sel.annotation.set_text(
                f"{sel.target[1]:.2f} €\n{dt.strftime('%Y-%m-%d')}"
            )

        cursor.connect("add", _fmt)

        # ── os X: največ 6–8 kljukic + rotacija ──────────────────────
        max_ticks = 8
        locator_cls = getattr(mdates, "AutoDateLocator", None)
        if locator_cls is not None:
            locator = locator_cls(minticks=3, maxticks=max_ticks)
        else:
            locator = mdates.DayLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
        fig.autofmt_xdate(rotation=45, ha="right")

        # malenkost več prostora
        if hasattr(fig, "set_size_inches"):
            fig.set_size_inches(5.5, 3.2)
        if hasattr(fig, "tight_layout"):
            fig.tight_layout(pad=1.0)
        if hasattr(ax, "ticklabel_format"):
            try:
                ax.ticklabel_format(useOffset=False, style="plain")
            except Exception:  # pragma: no cover - depends on matplotlib version
                pass
        if hasattr(ax, "get_yaxis") and hasattr(ax.get_yaxis(), "get_major_formatter"):
            formatter = ax.get_yaxis().get_major_formatter()
            if hasattr(formatter, "set_useOffset"):
                formatter.set_useOffset(False)
        min_v, max_v = float(price_series.min()), float(price_series.max())
        if min_v == max_v:
            pad = abs(min_v) * 0.03
            if pad == 0:
                pad = 0.10
        else:
            pad = (max_v - min_v) * 0.10
        ax.set_ylim(min_v - pad, max_v + pad)
        ax.set_xlabel("Datum")
        ax.set_ylabel("Cena")
        ax.set_title("Dnevno povprečje")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:.2f}"))
        ax.grid(True, linestyle=":")
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

