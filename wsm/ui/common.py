# File: wsm/ui/common.py
"""Helper functions shared by GUI components."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from decimal import Decimal

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox

from wsm.analyze import analyze_invoice
from wsm.parsing.pdf import parse_pdf, get_supplier_name_from_pdf
from wsm.parsing.eslog import get_supplier_name
from wsm.utils import sanitize_folder_name, _load_supplier_map
from wsm.ui.review_links import review_links


def select_invoice() -> Path | None:
    """Open a file dialog and return the chosen path."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Izberite e-račun",
        filetypes=[("e-računi", "*.xml *.pdf"), ("XML", "*.xml"), ("PDF", "*.pdf")],
    )
    root.destroy()
    return Path(file_path) if file_path else None


def open_invoice_gui(
    invoice_path: Path,
    suppliers: Path | None = None,
    wsm_codes: Path | None = None,
    keywords: Path | None = None,
) -> None:
    """Parse invoice and launch the review GUI.

    If ``suppliers`` or ``wsm_codes`` is not provided, the function reads the
    paths from environment variables ``WSM_SUPPLIERS`` and ``WSM_CODES``.
    When neither is set, it falls back to ``links`` and ``sifre_wsm.xlsx`` in
    the current working directory.
    """

    if suppliers is None:
        suppliers = Path(os.getenv("WSM_SUPPLIERS", "links"))
    if wsm_codes is None:
        wsm_codes = Path(os.getenv("WSM_CODES", "sifre_wsm.xlsx"))
    if keywords is None:
        keywords = Path(os.getenv("WSM_KEYWORDS", "kljucne_besede_wsm_kode.xlsx"))
    try:
        if invoice_path.suffix.lower() == ".xml":
            df, total, _ = analyze_invoice(str(invoice_path), str(suppliers))

            if "rabata" in df.columns:
                df["rabata"] = df["rabata"].fillna(Decimal("0"))
            else:
                df["rabata"] = Decimal("0")

        elif invoice_path.suffix.lower() == ".pdf":
            df = parse_pdf(str(invoice_path))
            if "rabata" not in df.columns:
                df["rabata"] = Decimal("0")
            total = df["vrednost"].sum()
        else:
            messagebox.showerror("Napaka", f"Nepodprta datoteka: {invoice_path}")
            return
    except Exception as exc:
        messagebox.showerror("Napaka", str(exc))
        return

    from wsm.utils import main_supplier_code

    supplier_code = main_supplier_code(df) or "unknown"
    sup_map = _load_supplier_map(Path(suppliers))
    map_vat = sup_map.get(supplier_code, {}).get("vat") if sup_map else None
    vat = None
    if invoice_path.suffix.lower() == ".xml":
        from wsm.parsing.eslog import get_supplier_info_vat

        name = get_supplier_name(invoice_path) or supplier_code
        _, _, vat_num = get_supplier_info_vat(invoice_path)
        if vat_num:
            vat = vat_num
    elif invoice_path.suffix.lower() == ".pdf":
        name = get_supplier_name_from_pdf(invoice_path) or supplier_code
    else:
        name = supplier_code
    if not vat and map_vat:
        vat = map_vat
    safe_id = sanitize_folder_name(vat or name)
    links_dir = suppliers / safe_id
    links_dir.mkdir(parents=True, exist_ok=True)
    links_file = links_dir / f"{supplier_code}_{safe_id}_povezane.xlsx"

    sifre_file = wsm_codes
    if sifre_file.exists():
        try:
            wsm_df = pd.read_excel(sifre_file, dtype=str)
        except Exception as exc:
            logging.warning(f"Napaka pri branju {sifre_file}: {exc}")
            wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])
    else:
        wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    try:
        from wsm.utils import povezi_z_wsm

        df = povezi_z_wsm(df, str(sifre_file), str(keywords), suppliers, supplier_code)
    except Exception as exc:
        logging.warning(f"Napaka pri samodejnem povezovanju: {exc}")

    review_links(df, wsm_df, links_file, total, invoice_path)

