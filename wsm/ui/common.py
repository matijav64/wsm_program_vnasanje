# File: wsm/ui/common.py
"""Helper functions shared by GUI components."""
from __future__ import annotations

import logging
from pathlib import Path
from decimal import Decimal

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox

from wsm.analyze import analyze_invoice
from wsm.parsing.pdf import parse_pdf, get_supplier_name_from_pdf
from wsm.parsing.eslog import get_supplier_name
from wsm.utils import sanitize_folder_name
from wsm.ui.review_links import review_links

logging.basicConfig(level=logging.INFO)


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


def open_invoice_gui(invoice_path: Path, suppliers: Path = Path("links")) -> None:
    """Parse invoice and launch the review GUI."""
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
    if invoice_path.suffix.lower() == ".xml":
        name = get_supplier_name(invoice_path) or supplier_code
    elif invoice_path.suffix.lower() == ".pdf":
        name = get_supplier_name_from_pdf(invoice_path) or supplier_code
    else:
        name = supplier_code
    safe_name = sanitize_folder_name(name)
    links_dir = suppliers / safe_name
    links_dir.mkdir(parents=True, exist_ok=True)
    links_file = links_dir / f"{supplier_code}_{safe_name}_povezane.xlsx"

    sifre_file = Path("sifre_wsm.xlsx")
    if sifre_file.exists():
        try:
            wsm_df = pd.read_excel(sifre_file, dtype=str)
        except Exception as exc:
            logging.warning(f"Napaka pri branju {sifre_file}: {exc}")
            wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])
    else:
        wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    review_links(df, wsm_df, links_file, total, invoice_path)

