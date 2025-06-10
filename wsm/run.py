"""Entry point for launching WSM in GUI mode or as CLI."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox

from wsm.cli import main as cli_main
from wsm.analyze import analyze_invoice
from wsm.parsing.pdf import parse_pdf
from wsm.ui.review_links import review_links

logging.basicConfig(level=logging.INFO)


def _select_invoice() -> Path | None:
    """Open a file dialog and return the chosen path."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Izberite e-račun",
        filetypes=[("e-računi", "*.xml *.pdf"), ("XML", "*.xml"), ("PDF", "*.pdf")],
    )
    root.destroy()
    return Path(file_path) if file_path else None


def _open_gui(invoice_path: Path) -> None:
    """Parse invoice and launch the review GUI."""
    try:
        if invoice_path.suffix.lower() == ".xml":
            df, total, _ = analyze_invoice(str(invoice_path))
        elif invoice_path.suffix.lower() == ".pdf":
            df = parse_pdf(str(invoice_path))
            total = df["vrednost"].sum()
        else:
            messagebox.showerror("Napaka", f"Nepodprta datoteka: {invoice_path}")
            return
    except Exception as exc:
        messagebox.showerror("Napaka", str(exc))
        return

    supplier_code = df["sifra_dobavitelja"].iloc[0] if not df.empty else "unknown"
    links_dir = Path("links")
    links_dir.mkdir(exist_ok=True)
    links_file = links_dir / f"{supplier_code}_povezave.xlsx"

    # WSM codes are optional; start with an empty table
    wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])
    review_links(df, wsm_df, links_file, total)


def main() -> None:
    if len(sys.argv) > 1:
        cli_main()
    else:
        invoice = _select_invoice()
        if invoice:
            _open_gui(invoice)


if __name__ == "__main__":
    main()
