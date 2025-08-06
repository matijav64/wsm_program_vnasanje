# File: wsm/ui/review/gui.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox
from lxml import etree as LET

from wsm.utils import short_supplier_name, _clean, _build_header_totals
from wsm.constants import PRICE_DIFF_THRESHOLD
from .helpers import (
    _fmt,
    _norm_unit,
    _merge_same_items,
    _apply_price_warning,
)
from .io import _save_and_close, _load_supplier_map

# Logger setup
log = logging.getLogger(__name__)


def review_links(
    df: pd.DataFrame,
    wsm_df: pd.DataFrame,
    links_file: Path,
    invoice_total: Decimal,
    invoice_path: Path | None = None,
    price_warn_pct: float | int | Decimal | None = None,
) -> pd.DataFrame:
    """Interactively map supplier invoice rows to WSM items.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame with invoice line details which will be modified in-place.
    wsm_df : pandas.DataFrame
        Table of available WSM articles with codes and names.
    links_file : pathlib.Path
        Excel file containing saved mappings for the supplier.
    invoice_total : decimal.Decimal
        Net total of the invoice used for validating the amounts.
    invoice_path : pathlib.Path, optional
        Path to the invoice document from which additional metadata (date,
        invoice number, supplier name) may be extracted.
    price_warn_pct : float | int | Decimal, optional
        Threshold for price change warnings expressed in percent. When not
        provided, the value of ``PRICE_DIFF_THRESHOLD`` is used.

    Returns
    -------
    pandas.DataFrame
        The reviewed invoice lines including any document-level correction
        rows.
    """
    df = df.copy()
    log.debug("Initial invoice DataFrame:\n%s", df.to_string())
    if {"cena_bruto", "cena_netto"}.issubset(df.columns):
        for idx, row in df.iterrows():
            log.info(
                "XML[%s] bruto=%s neto=%s ddv=%s",
                idx,
                row.get("cena_bruto"),
                row.get("cena_netto"),
                row.get("ddv"),
            )
    price_warn_threshold = (
        Decimal(str(price_warn_pct))
        if price_warn_pct is not None
        else PRICE_DIFF_THRESHOLD
    )
    supplier_code: str | None = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            tree = LET.parse(invoice_path)
            root = tree.getroot()
            ns = {k: v for k, v in root.nsmap.items() if k}
            vat_vals = root.xpath(
                ".//cac:PartyTaxScheme/cbc:CompanyID/text()"
                "|.//*[@schemeID='VA' or @schemeID='VAT']/text()",
                namespaces=ns,
            )
            if vat_vals:
                supplier_code = vat_vals[0].strip()
                log.debug("Supplier VAT from invoice: %s", supplier_code)
            else:
                from wsm.parsing.eslog import get_supplier_info

                supplier_code, _ = get_supplier_info(invoice_path)
                log.debug("Supplier code from invoice: %s", supplier_code)
        except Exception as exc:
            log.debug("Supplier code lookup failed: %s", exc)
    if not supplier_code:
        supplier_code = links_file.stem.split("_")[0]
    suppliers_file = links_file.parent.parent
    log.debug(f"Pot do mape links: {suppliers_file}")
    sup_map = _load_supplier_map(suppliers_file)

    log.info("Resolved supplier code: %s", supplier_code)
    supplier_info = sup_map.get(supplier_code, {})
    default_name = short_supplier_name(supplier_info.get("ime", supplier_code))
    supplier_vat = supplier_info.get("vat")

    service_date = None
    invoice_number = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import (
                extract_service_date,
                extract_invoice_number,
            )

            service_date = extract_service_date(invoice_path)
            invoice_number = extract_invoice_number(invoice_path)
        except Exception as exc:
            log.warning(f"Napaka pri branju glave računa: {exc}")
    elif invoice_path and invoice_path.suffix.lower() == ".pdf":
        try:
            from wsm.parsing.pdf import (
                extract_service_date,
                extract_invoice_number,
            )

            service_date = extract_service_date(invoice_path)
            invoice_number = extract_invoice_number(invoice_path)
        except Exception as exc:
            log.warning(f"Napaka pri branju glave računa: {exc}")

    inv_name = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import (
                get_supplier_name,
                get_supplier_info_vat,
            )

            inv_name = get_supplier_name(invoice_path)
            if not supplier_vat:
                _, _, vat = get_supplier_info_vat(invoice_path)
                supplier_vat = vat
        except Exception:
            inv_name = None
    elif invoice_path and invoice_path.suffix.lower() == ".pdf":
        try:
            from wsm.parsing.pdf import get_supplier_name_from_pdf

            inv_name = get_supplier_name_from_pdf(invoice_path)
        except Exception:
            inv_name = None
    if inv_name:
        default_name = short_supplier_name(inv_name)

    log.info(f"Default name retrieved: {default_name}")
    log.debug(f"Supplier info: {supplier_info}")

    header_totals = _build_header_totals(invoice_path, invoice_total)

    try:
        manual_old = pd.read_excel(links_file, dtype=str)
        log.info("Processing complete")
        log.info(
            f"Število prebranih povezav iz {links_file}: {len(manual_old)}"
        )
        log.debug(
            f"Primer povezav iz {links_file}: {manual_old.head().to_dict()}"
        )
        manual_old["sifra_dobavitelja"] = (
            manual_old["sifra_dobavitelja"].fillna("").astype(str)
        )
        empty_sifra_old = manual_old["sifra_dobavitelja"].eq("")
        if empty_sifra_old.any():
            log.warning(
                "Prazne vrednosti v sifra_dobavitelja v manual_old za "
                f"{empty_sifra_old.sum()} vrstic"
            )
            sample = manual_old[empty_sifra_old][
                ["naziv", "sifra_dobavitelja"]
            ]
            log.debug(
                "Primer vrstic s prazno sifra_dobavitelja: %s",
                sample.head().to_dict(),
            )
        manual_old["naziv_ckey"] = manual_old["naziv"].map(_clean)
    except Exception as e:
        manual_old = pd.DataFrame(
            columns=[
                "sifra_dobavitelja",
                "naziv",
                "wsm_sifra",
                "dobavitelj",
                "naziv_ckey",
            ]
        )
        log.debug(
            "Manual_old ni obstajal ali napaka pri branju: %s, "
            "ustvarjam prazen DataFrame",
            e,
        )

    existing_names = sorted(
        {
            short_supplier_name(n)
            for n in manual_old.get("dobavitelj", [])
            if isinstance(n, str) and n.strip()
        }
    )
    supplier_name = short_supplier_name(default_name)
    if supplier_name and supplier_name not in existing_names:
        existing_names.insert(0, supplier_name)
    supplier_name = existing_names[0] if existing_names else supplier_code
    df["dobavitelj"] = supplier_name
    log.debug(f"Supplier name nastavljen na: {supplier_name}")

    # Normalize codes before lookup
    df["sifra_dobavitelja"] = df["sifra_dobavitelja"].fillna("").astype(str)
    empty_sifra = df["sifra_dobavitelja"] == ""
    if empty_sifra.any():
        log.warning(
            "Prazne vrednosti v sifra_dobavitelja za "
            f"{empty_sifra.sum()} vrstic v df",
        )

    # Create a dictionary for quick lookup
    old_map_dict = manual_old.set_index(["sifra_dobavitelja", "naziv_ckey"])[
        "wsm_sifra"
    ].to_dict()
    old_unit_dict = {}
    if "enota_norm" in manual_old.columns:
        old_unit_dict = manual_old.set_index(
            ["sifra_dobavitelja", "naziv_ckey"]
        )["enota_norm"].to_dict()

    df["naziv_ckey"] = df["naziv"].map(_clean)
    df["wsm_sifra"] = df.apply(
        lambda r: old_map_dict.get(
            (r["sifra_dobavitelja"], r["naziv_ckey"]), pd.NA
        ),
        axis=1,
    )
    df["wsm_naziv"] = df["wsm_sifra"].map(
        wsm_df.set_index("wsm_sifra")["wsm_naziv"]
    )
    df["status"] = (
        df["wsm_sifra"].notna().map({True: "POVEZANO", False: pd.NA})
    )
    log.debug(f"df po inicializaciji: {df.head().to_dict()}")

    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    doc_discount_raw = df_doc["vrednost"].sum()
    doc_discount = (
        doc_discount_raw
        if isinstance(doc_discount_raw, Decimal)
        else Decimal(str(doc_discount_raw))
    )
    log.debug("df before _DOC_ filter:\n%s", df.to_string())
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
    doc_discount_total = doc_discount  # backward compatibility
    df["ddv"] = df["ddv"].apply(
        lambda x: Decimal(str(x)) if not isinstance(x, Decimal) else x
    )  # ensure VAT values are Decimal for accurate totals
    # Ensure a clean sequential index so Treeview item IDs are predictable
    df = df.reset_index(drop=True)
    df["cena_pred_rabatom"] = df.apply(
        lambda r: (
            (r["vrednost"] + r["rabata"]) / r["kolicina"]
            if r["kolicina"]
            else Decimal("0")
        ),
        axis=1,
    )
    df["cena_po_rabatu"] = df.apply(
        lambda r: (
            r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    df["rabata_pct"] = df.apply(
        lambda r: (
            (
                (r["rabata"] / (r["vrednost"] + r["rabata"])) * Decimal("100")
            ).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if (r["vrednost"] + r["rabata"])
            else Decimal("0.00")
        ),
        axis=1,
    )
    df["total_net"] = df["vrednost"]
    net_total = df["total_net"].sum().quantize(Decimal("0.01"))
    df["is_gratis"] = df["rabata_pct"] >= Decimal("99.9")
    df["kolicina_norm"], df["enota_norm"] = zip(
        *[
            _norm_unit(Decimal(str(q)), u, n, vat, code)
            for q, u, n, vat, code in zip(
                df["kolicina"],
                df["enota"],
                df["naziv"],
                df["ddv_stopnja"],
                df.get("sifra_artikla"),
            )
        ]
    )
    if old_unit_dict:
        log.debug(f"Old unit mapping loaded: {old_unit_dict}")

        def _restore_unit(r):
            return old_unit_dict.get(
                (r["sifra_dobavitelja"], r["naziv_ckey"]), r["enota_norm"]
            )

        before = df["enota_norm"].copy()
        df["enota_norm"] = df.apply(_restore_unit, axis=1)
        changed = (before != df["enota_norm"]).sum()
        log.debug(f"Units restored from old map: {changed} rows updated")

        log.debug(
            "Units after applying saved mapping: %s",
            df["enota_norm"].value_counts().to_dict(),
        )

    # Keep ``kolicina_norm`` as ``Decimal`` to avoid losing precision in
    # subsequent calculations and when saving the file. Previously the column
    # was cast to ``float`` which could introduce rounding errors.
    df["warning"] = pd.NA
    log.debug("df po normalizaciji: %s", df.head().to_dict())

    # Combine duplicate invoice lines except for gratis items
    df = _merge_same_items(df)

    root = tk.Tk()
    # Window title shows the full supplier name while the on-screen
    # header can be a bit shorter for readability.
    root.title(f"Ročna revizija – {supplier_name}")

    # Determine how many rows can fit based on the screen height. Roughly
    # 500px is taken by the header, summary and button sections so we convert
    # the remaining space to a row count assuming ~20px per row.
    screen_height = root.winfo_screenheight()
    tree_height = max(10, (screen_height - 500) // 20)
    # Start maximized but keep the window decorations visible
    try:
        root.state("zoomed")
    except tk.TclError:
        pass

    # Limit supplier name to 20 characters in the GUI header

    display_name = supplier_name[:20]
    header_var = tk.StringVar()
    supplier_var = tk.StringVar()
    date_var = tk.StringVar()
    invoice_var = tk.StringVar()
    var_net = tk.StringVar()
    var_vat = tk.StringVar()
    var_total = tk.StringVar()

    def _refresh_header():
        parts_full = [supplier_name]
        parts_display = [display_name]
        if service_date:
            date_txt = str(service_date)
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_txt):
                y, m, d = date_txt.split("-")
                date_txt = f"{d}.{m}.{y}"
            elif re.match(r"^\d{8}$", date_txt):
                y, m, d = date_txt[:4], date_txt[4:6], date_txt[6:8]
                date_txt = f"{d}.{m}.{y}"
            parts_full.append(date_txt)
            parts_display.append(date_txt)
            date_var.set(date_txt)
        else:
            # Do not clear the value if ``service_date`` is missing so
            # previously set text in ``date_var`` remains visible.
            pass
        if invoice_number:
            parts_full.append(str(invoice_number))
            parts_display.append(str(invoice_number))
            invoice_var.set(str(invoice_number))
        else:
            # Preserve any existing invoice number displayed in the entry.
            pass
        supplier_var.set(supplier_name)
        header_var.set(" – ".join(parts_display))
        root.title(f"Ročna revizija – {' – '.join(parts_full)}")
        log.debug(
            f"_refresh_header: supplier_var={supplier_var.get()}, "
            f"date_var={date_var.get()}, invoice_var={invoice_var.get()}"
        )

    def _refresh_header_totals():
        var_net.set(_fmt(header_totals["net"]))
        var_vat.set(_fmt(header_totals["vat"]))
        var_total.set(_fmt(header_totals["gross"]))

    header_lbl = tk.Label(
        root,
        textvariable=header_var,
        font=("Arial", 24, "bold"),
        anchor="center",
        justify="center",
        pady=0,  # eliminate internal padding
    )
    # Remove extra space so the buttons sit right under the title
    header_lbl.pack(fill="x", pady=(0, 0))

    info_frame = tk.Frame(root)
    # Keep the buttons tight to the header but leave extra room below
    info_frame.pack(anchor="w", padx=8, pady=(0, 12))

    def _copy(val: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(val)

    tk.Button(
        info_frame,
        text="Kopiraj dobavitelja",
        command=lambda: _copy(supplier_var.get()),
    ).grid(row=0, column=0, sticky="w", padx=(0, 4))
    tk.Button(
        info_frame,
        text="Kopiraj storitev",
        command=lambda: _copy(date_var.get()),
    ).grid(row=0, column=1, sticky="w", padx=(0, 4))
    tk.Button(
        info_frame,
        text="Kopiraj številko računa",
        command=lambda: _copy(invoice_var.get()),
    ).grid(row=0, column=2, sticky="w")

    # Refresh header once widgets exist. ``after_idle`` ensures widgets are
    # fully initialized before values are set so the entries show up
    root.after_idle(_refresh_header)
    root.after_idle(_refresh_header_totals)
    log.debug(
        f"after_idle scheduled: supplier_var={supplier_var.get()}, "
        f"date_var={date_var.get()}, invoice_var={invoice_var.get()}"
    )

    totals_frame = tk.Frame(root)
    totals_frame.pack(anchor="w", padx=8, pady=(0, 12))
    tk.Label(totals_frame, text="Neto:").grid(row=0, column=0, sticky="w")
    tk.Label(totals_frame, textvariable=var_net).grid(
        row=0, column=1, sticky="w", padx=(0, 12)
    )
    tk.Label(totals_frame, text="DDV:").grid(row=0, column=2, sticky="w")
    tk.Label(totals_frame, textvariable=var_vat).grid(
        row=0, column=3, sticky="w", padx=(0, 12)
    )
    tk.Label(totals_frame, text="Skupaj:").grid(row=0, column=4, sticky="w")
    tk.Label(totals_frame, textvariable=var_total).grid(
        row=0, column=5, sticky="w"
    )

    # Allow Escape to restore the original window size
    root.bind("<Escape>", lambda e: root.state("normal"))

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
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
    heads = [
        "Naziv artikla",
        "Količina",
        "Enota",
        "Rabat (%)",
        "Net. pred rab.",
        "Net. po rab.",
        "Skupna neto",
        "Opozorilo",
        "WSM naziv",
        "Dobavitelj",
    ]
    tree = ttk.Treeview(
        frame, columns=cols, show="headings", height=tree_height
    )
    tree.tag_configure("price_warn", background="orange")
    tree.tag_configure("gratis", background="#ffe6cc")  # oranžna
    tree.tag_configure("linked", background="#ffe6cc")
    tree.tag_configure("suggestion", background="#ffe6cc")
    tree.tag_configure("autofix", background="#eeeeee", foreground="#444")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    for c, h in zip(cols, heads):
        tree.heading(c, text=h)
        width = (
            300
            if c == "naziv"
            else 80 if c == "enota_norm" else 160 if c == "warning" else 120
        )
        tree.column(c, width=width, anchor="w")
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
        log.info(
            "GRID[%s] cena_po_rabatu=%s",
            i,
            row.get("cena_po_rabatu"),
        )
        label = f"{row['sifra_dobavitelja']} - {row['naziv']}"
        try:
            from wsm.utils import load_last_price

            prev_price = load_last_price(label, suppliers_file)
        except Exception as exc:  # pragma: no cover - robust against IO errors
            log.warning("Napaka pri branju zadnje cene: %s", exc)
            prev_price = None

        warn, tooltip = _apply_price_warning(
            row["cena_po_rabatu"],
            prev_price,
            threshold=price_warn_threshold,
        )
        tree.item(str(i), tags=("price_warn",) if warn else ())
        df.at[i, "warning"] = tooltip
        if "is_gratis" in row and row["is_gratis"]:
            current_tags = tree.item(str(i)).get("tags", ())
            if not isinstance(current_tags, tuple):
                current_tags = (current_tags,) if current_tags else ()
            #  ➜ 'gratis' naj bo PRVI, da barva vedno prime
            tree.item(str(i), tags=("gratis",) + current_tags)

            #  ➜ besedilo v stolpcu »Opozorilo«
            tree.set(str(i), "warning", "GRATIS")
    tree.focus("0")
    tree.selection_set("0")

    # Povzetek skupnih neto cen po WSM šifrah
    summary_frame = tk.Frame(root)
    summary_frame.pack(fill="both", expand=True, pady=10)
    tk.Label(
        summary_frame,
        text="Povzetek po WSM šifrah",
        font=("Arial", 12, "bold"),
    ).pack()

    summary_cols = [
        "wsm_sifra",
        "wsm_naziv",
        "kolicina_norm",
        "neto_brez_popusta",
        "rabata_pct",
        "vrednost",
    ]
    summary_heads = [
        "WSM Šifra",
        "WSM Naziv",
        "Količina",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    ]
    summary_tree = ttk.Treeview(
        summary_frame, columns=summary_cols, show="headings", height=5
    )
    vsb_summary = ttk.Scrollbar(
        summary_frame, orient="vertical", command=summary_tree.yview
    )
    summary_tree.configure(yscrollcommand=vsb_summary.set)
    vsb_summary.pack(side="right", fill="y")
    summary_tree.pack(side="left", fill="both", expand=True)

    for c, h in zip(summary_cols, summary_heads):
        summary_tree.heading(c, text=h)
        summary_tree.column(c, width=150, anchor="w")

    def _update_summary():
        for item in summary_tree.get_children():
            summary_tree.delete(item)
        required = {
            "wsm_sifra",
            "vrednost",
            "rabata",
            "kolicina_norm",
            "rabata_pct",
        }
        if required.issubset(df.columns):
            summary_df = (
                df[df["wsm_sifra"].notna()]
                .groupby(["wsm_sifra", "rabata_pct"], dropna=False)
                .agg(
                    {
                        "vrednost": "sum",
                        "rabata": "sum",
                        "kolicina_norm": "sum",
                    }
                )
                .reset_index()
            )

            summary_df["neto_brez_popusta"] = (
                summary_df["vrednost"] + summary_df["rabata"]
            )
            summary_df["wsm_naziv"] = summary_df["wsm_sifra"].map(
                wsm_df.set_index("wsm_sifra")["wsm_naziv"]
            )
            summary_df["rabata_pct"] = [
                (
                    (
                        row["rabata"]
                        / row["neto_brez_popusta"]
                        * Decimal("100")
                    ).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    if row["neto_brez_popusta"]
                    else Decimal("0.00")
                )
                for _, row in summary_df.iterrows()
            ]

            for _, row in summary_df.iterrows():
                vals = [
                    row["wsm_sifra"],
                    row["wsm_naziv"],
                    _fmt(row["kolicina_norm"]),
                    _fmt(row["neto_brez_popusta"]),
                    _fmt(row["rabata_pct"]),
                    _fmt(row["vrednost"]),
                ]
                summary_tree.insert("", "end", values=vals)
                log.info(
                    "SUMMARY[%s] cena=%s",
                    row["wsm_sifra"],
                    row.get("vrednost"),
                )
            log.debug(f"Povzetek posodobljen: {len(summary_df)} WSM šifer")

    # Skupni zneski pod povzetkom
    total_frame = tk.Frame(root)
    total_frame.pack(fill="x", pady=5)

    vat_val = header_totals["vat"]
    if not isinstance(vat_val, Decimal):
        vat_val = Decimal(str(vat_val))
    vat_total = vat_val.quantize(Decimal("0.01"))
    gross = net_total + vat_total
    inv_total = (
        header_totals["gross"]
        if isinstance(header_totals["gross"], Decimal)
        else Decimal(str(header_totals["gross"]))
    )
    tolerance = Decimal("0.01")
    diff = inv_total - gross
    if abs(diff) > tolerance:
        if doc_discount:
            diff2 = inv_total - (gross + abs(doc_discount))
            if abs(diff2) > tolerance:
                messagebox.showwarning(
                    "Opozorilo",
                    (
                        "Razlika med postavkami in računom je "
                        f"{diff2:+.2f} € in presega dovoljeno zaokroževanje."
                    ),
                )
        else:
            messagebox.showwarning(
                "Opozorilo",
                (
                    "Razlika med postavkami in računom je "
                    f"{diff:+.2f} € in presega dovoljeno zaokroževanje."
                ),
            )
    net = net_total
    vat = vat_total

    lbl_totals = tk.Label(
        total_frame,
        text=(
            f"Neto:   {net:,.2f} €\n"
            f"DDV:    {vat:,.2f} €\n"
            f"Skupaj: {gross:,.2f} €"
        ),
        font=("Arial", 10, "bold"),
        name="total_sum",
        justify="left",
    )
    lbl_totals.pack(side="left", padx=10)

    def _update_totals():
        net_raw = df["total_net"].sum()
        net_total = (
            Decimal(str(net_raw))
            if not isinstance(net_raw, Decimal)
            else net_raw
        ).quantize(Decimal("0.01"))
        vat_val = header_totals["vat"]
        if not isinstance(vat_val, Decimal):
            vat_val = Decimal(str(vat_val))
        vat_val = vat_val.quantize(Decimal("0.01"))
        calc_total = net_total + vat_val
        inv_total = (
            header_totals["gross"]
            if isinstance(header_totals["gross"], Decimal)
            else Decimal(str(header_totals["gross"]))
        )
        tolerance = Decimal("0.01")
        diff = inv_total - calc_total
        try:
            discount = doc_discount
        except NameError:  # backward compatibility
            discount = doc_discount_total
        if abs(diff) > tolerance:
            if discount:
                diff2 = inv_total - (calc_total + abs(discount))
                if abs(diff2) > tolerance:
                    messagebox.showwarning(
                        "Opozorilo",
                        (
                            "Razlika med postavkami in računom je "
                            f"{diff2:+.2f} € in presega "
                            "dovoljeno zaokroževanje."
                        ),
                    )
            else:
                messagebox.showwarning(
                    "Opozorilo",
                    (
                        "Razlika med postavkami in računom je "
                        f"{diff:+.2f} € in presega "
                        "dovoljeno zaokroževanje."
                    ),
                )

        net = net_total
        vat = vat_val
        gross = calc_total
        total_frame.children["total_sum"].config(
            text=(
                f"Neto:   {net:,.2f} €\n"
                f"DDV:    {vat:,.2f} €\n"
                f"Skupaj: {gross:,.2f} €"
            )
        )

    bottom = tk.Frame(root)
    bottom.pack(fill="x", padx=8, pady=6)

    custom = tk.Frame(bottom)
    custom.pack(side="left", fill="x", expand=True)
    tk.Label(custom, text="Vpiši / izberi WSM naziv:").pack(side="left")
    entry = tk.Entry(custom)
    entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
    lb = tk.Listbox(custom, height=6)

    btn_frame = tk.Frame(bottom)
    btn_frame.pack(side="right")

    # --- Unit change widgets ---
    unit_options = ["kos", "kg", "L"]

    def _finalize_and_save(_=None):
        _update_summary()
        _update_totals()
        _save_and_close(
            df,
            manual_old,
            wsm_df,
            links_file,
            root,
            supplier_name,
            supplier_code,
            sup_map,
            suppliers_file,
            invoice_path=invoice_path,
            vat=supplier_vat,
        )

    save_btn = tk.Button(
        btn_frame,
        text="Shrani & zapri",
        width=14,
        command=_finalize_and_save,
    )

    def _exit():
        root.quit()

    exit_btn = tk.Button(
        btn_frame,
        text="Izhod",
        width=14,
        command=_exit,
    )
    exit_btn.pack(side="right", padx=(6, 0))
    save_btn.pack(side="right", padx=(6, 0))

    root.bind("<F10>", _finalize_and_save)

    nazivi = wsm_df["wsm_naziv"].dropna().tolist()
    n2s = dict(zip(wsm_df["wsm_naziv"], wsm_df["wsm_sifra"]))

    def _start_edit(_=None):
        if not tree.focus():
            return "break"
        entry.delete(0, "end")
        lb.pack_forget()
        entry.focus_set()
        return "break"

    def _suggest(evt=None):
        if evt and evt.keysym in {
            "Return",
            "Escape",
            "Up",
            "Down",
            "Tab",
            "Right",
            "Left",
        }:
            return
        txt = entry.get().strip().lower()
        lb.delete(0, "end")
        if not txt:
            lb.pack_forget()
            return
        matches = [n for n in nazivi if txt in n.lower()]
        if matches:
            lb.pack(fill="x")
            for m in matches:
                lb.insert("end", m)
            lb.selection_set(0)
            lb.activate(0)
            lb.see(0)
        else:
            lb.pack_forget()

    def _init_listbox(evt=None):
        """Give focus to the listbox and handle initial navigation."""
        if lb.winfo_ismapped():
            lb.focus_set()
            if not lb.curselection():
                lb.selection_set(0)
                lb.activate(0)
                lb.see(0)
            if evt and evt.keysym == "Down":
                _nav_list(evt)
        return "break"

    def _nav_list(evt):
        cur = lb.curselection()[0] if lb.curselection() else -1
        nxt = cur + 1 if evt.keysym == "Down" else cur - 1
        nxt = max(0, min(lb.size() - 1, nxt))
        lb.selection_clear(0, "end")
        lb.selection_set(nxt)
        lb.activate(nxt)
        lb.see(nxt)
        return "break"

    def _edit_unit(evt):
        """Handle double-clicks on the tree view."""
        col = tree.identify_column(evt.x)
        row_id = tree.identify_row(evt.y)
        if col != "#3":
            log.debug("Double-click outside Enota column -> starting edit")
            return _start_edit()
        if not row_id:
            return
        idx = int(row_id)

        log.debug(
            "Editing row %s current unit=%s", idx, df.at[idx, "enota_norm"]
        )

        top = tk.Toplevel(root)
        top.title("Spremeni enoto")
        var = tk.StringVar(value=df.at[idx, "enota_norm"])
        cb = ttk.Combobox(
            top, values=unit_options, textvariable=var, state="readonly"
        )
        cb.pack(padx=10, pady=10)
        log.debug("Edit dialog opened with value %s", var.get())

        def _apply(_=None):
            new_u = var.get()
            before = df.at[idx, "enota_norm"]
            # Only change the normalized value so the original
            # invoice unit remains intact. ``enota`` is needed to
            # detect H87 when applying saved overrides.
            df.at[idx, "enota_norm"] = new_u
            tree.set(row_id, "enota_norm", new_u)

            log.info("Updated row %s unit from %s to %s", idx, before, new_u)
            log.debug("Combobox in edit dialog value: %s", cb.get())

            _update_summary()
            _update_totals()
            top.destroy()

        tk.Button(top, text="OK", command=_apply).pack(pady=(0, 10))
        cb.bind("<Return>", _apply)
        cb.focus_set()
        return "break"

    price_tip: tk.Toplevel | None = None
    last_warn_item: str | None = None

    def _hide_tooltip(_=None):
        nonlocal price_tip, last_warn_item
        if price_tip is not None:
            price_tip.destroy()
            price_tip = None
        if last_warn_item is not None:
            tags = ()
            idx = int(last_warn_item)
            if "is_gratis" in df.columns and df.at[idx, "is_gratis"]:
                tags = ("gratis",)
            tree.item(last_warn_item, tags=tags)
            last_warn_item = None

    def _show_tooltip(item_id: str, text: str | None) -> None:
        nonlocal price_tip, last_warn_item
        _hide_tooltip()
        if not text:
            return
        bbox = tree.bbox(item_id)
        if not bbox:
            return
        x, y, w, h = bbox
        price_tip = tk.Toplevel(root)
        price_tip.wm_overrideredirect(True)
        tk.Label(
            price_tip,
            text=text,
            background="#ffe6b3",
            relief="solid",
            borderwidth=1,
        ).pack()
        price_tip.geometry(f"+{tree.winfo_rootx()+x+w}+{tree.winfo_rooty()+y}")
        last_warn_item = item_id

    def _on_select(_=None):
        sel_i = tree.focus()
        if not sel_i:
            _hide_tooltip()
            return
        idx = int(sel_i)
        tooltip = df.at[idx, "warning"]
        _show_tooltip(sel_i, tooltip)

    def _confirm(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        choice = (
            lb.get(lb.curselection()[0])
            if lb.curselection()
            else entry.get().strip()
        )
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = choice
        df.at[idx, "wsm_sifra"] = n2s.get(choice, pd.NA)
        df.at[idx, "status"] = "POVEZANO"
        df.at[idx, "dobavitelj"] = supplier_name
        if (
            pd.isna(df.at[idx, "sifra_dobavitelja"])
            or df.at[idx, "sifra_dobavitelja"] == ""
        ):
            log.warning("Prazna sifra_dobavitelja pri vnosu vrstice")
        label = f"{df.at[idx, 'sifra_dobavitelja']} - {df.at[idx, 'naziv']}"
        try:
            from wsm.utils import load_last_price

            prev_price = load_last_price(label, suppliers_file)
        except Exception as exc:  # pragma: no cover - robust against IO errors
            log.warning("Napaka pri branju zadnje cene: %s", exc)
            prev_price = None

        warn, tooltip = _apply_price_warning(
            df.at[idx, "cena_po_rabatu"],
            prev_price,
            threshold=price_warn_threshold,
        )
        tree.item(sel_i, tags=("price_warn",) if warn else ())

        df.at[idx, "warning"] = tooltip

        _show_tooltip(sel_i, tooltip)
        if "is_gratis" in df.columns and df.at[idx, "is_gratis"]:
            current_tags = tree.item(sel_i).get("tags", ())
            if not isinstance(current_tags, tuple):
                current_tags = (current_tags,) if current_tags else ()
            tree.item(sel_i, tags=("gratis",) + current_tags)
            tree.set(sel_i, "warning", "GRATIS")

        new_vals = [
            (
                _fmt(df.at[idx, c])
                if isinstance(df.at[idx, c], (Decimal, float, int))
                else ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c]))
            )
            for c in cols
        ]
        tree.item(sel_i, values=new_vals)
        log.debug(
            "Potrjeno: idx=%s, wsm_naziv=%s, wsm_sifra=%s, "
            "sifra_dobavitelja=%s",
            idx,
            choice,
            df.at[idx, "wsm_sifra"],
            df.at[idx, "sifra_dobavitelja"],
        )
        _update_summary()  # Update summary after confirming
        _update_totals()  # Update totals after confirming
        entry.delete(0, "end")
        lb.pack_forget()
        tree.focus_set()
        next_i = tree.next(sel_i)
        if next_i:
            tree.selection_set(next_i)
            tree.focus(next_i)
            tree.see(next_i)
        return "break"

    def _clear_wsm_connection(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = pd.NA
        df.at[idx, "wsm_sifra"] = pd.NA
        df.at[idx, "status"] = pd.NA
        new_vals = [
            (
                _fmt(df.at[idx, c])
                if isinstance(df.at[idx, c], (Decimal, float, int))
                else ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c]))
            )
            for c in cols
        ]
        tree.item(sel_i, values=new_vals)
        log.debug(
            f"Povezava odstranjena: idx={idx}, wsm_naziv=NaN, wsm_sifra=NaN"
        )
        _update_summary()  # Update summary after clearing
        _update_totals()  # Update totals after clearing
        tree.focus_set()
        return "break"

    def _tree_nav_up(_=None):
        """Select previous row and ensure it is visible."""
        prev_item = tree.prev(tree.focus()) or tree.focus()
        tree.selection_set(prev_item)
        tree.focus(prev_item)
        tree.see(prev_item)
        return "break"

    def _tree_nav_down(_=None):
        """Select next row and ensure it is visible."""
        next_item = tree.next(tree.focus()) or tree.focus()
        tree.selection_set(next_item)
        tree.focus(next_item)
        tree.see(next_item)
        return "break"

    # Vezave za tipke na tree
    # Dvojni klik na stolpec "Enota" odpre urejanje enote,
    # drugje pa sprozi urejanje vnosa.
    tree.bind("<Return>", _start_edit)
    tree.bind("<BackSpace>", _clear_wsm_connection)
    tree.bind("<Up>", _tree_nav_up)
    tree.bind("<Down>", _tree_nav_down)
    tree.bind("<Double-Button-1>", _edit_unit)
    tree.bind("<<TreeviewSelect>>", _on_select)

    # Vezave za entry in lb
    entry.bind("<KeyRelease>", _suggest)
    entry.bind("<Down>", _init_listbox)
    entry.bind("<Tab>", _init_listbox)
    entry.bind("<Right>", _init_listbox)
    entry.bind("<Return>", _confirm)
    entry.bind(
        "<Escape>",
        lambda e: (
            lb.pack_forget(),
            entry.delete(0, "end"),
            tree.focus_set(),
            "break",
        ),
    )
    lb.bind("<Return>", _confirm)
    lb.bind("<Double-Button-1>", _confirm)
    lb.bind("<Down>", _nav_list)
    lb.bind("<Up>", _nav_list)

    # Prvič osveži
    _update_summary()
    _update_totals()

    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass

    return pd.concat([df, df_doc], ignore_index=True)
