# File: wsm/ui/common.py
"""Helper functions shared by GUI components."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from decimal import Decimal

import tkinter as tk
from tkinter import filedialog, messagebox

from wsm.analyze import analyze_invoice
from lxml import etree as LET
from wsm.parsing.pdf import parse_pdf, get_supplier_name_from_pdf  # noqa: F401
from wsm.parsing.eslog import (  # noqa: F401
    get_supplier_name,
    extract_grand_total,
    parse_eslog_invoice,
    parse_invoice_totals,
)
import pandas as pd
from wsm.io import load_catalog, load_keywords_map
from wsm.io.wsm_catalog import KEYWORD_ALIAS_MAP, _rename_with_aliases
from wsm.utils import sanitize_folder_name, _load_supplier_map
from wsm.supplier_store import choose_supplier_key
from wsm.ui.review.gui import review_links

TRACE = os.getenv("WSM_TRACE", "0") not in {"0", "false", "False"}
_log = logging.getLogger(__name__)


def _t(msg, *args):
    if TRACE:
        _log.warning("[TRACE COMMON] " + msg, *args)


def select_invoice() -> Path | None:
    """Open a file dialog and return the chosen path."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Izberite e-račun",
        filetypes=[
            ("e-računi", "*.xml *.pdf"),
            ("XML", "*.xml"),
            ("PDF", "*.pdf"),
        ],
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
    paths from environment variables ``WSM_LINKS_DIR`` and ``WSM_CODES_FILE``.
    When neither is set, it falls back to ``links`` and ``sifre_wsm.xlsx`` in
    the current working directory.
    """

    if suppliers is None:
        suppliers = Path(os.getenv("WSM_LINKS_DIR", "links"))
    if wsm_codes is None:
        wsm_codes = Path(os.getenv("WSM_CODES_FILE", "sifre_wsm.xlsx"))
    if keywords is None:
        keywords = Path(
            os.getenv("WSM_KEYWORDS_FILE", "kljucne_besede_wsm_kode.xlsx")
        )
    try:
        if invoice_path.suffix.lower() == ".xml":
            keep_lines = os.getenv("WSM_GUI_KEEP_LINES", "1") not in {
                "0",
                "false",
                "False",
            }
            if keep_lines:
                try:
                    # parse_eslog_invoice lahko vrne DataFrame ALI (DataFrame, meta)
                    parsed = parse_eslog_invoice(invoice_path)
                    if isinstance(parsed, tuple):
                        df = parsed[0]
                    else:
                        df = parsed
                    if getattr(df, "empty", True):
                        raise ValueError("no lines parsed")
                    # parse_invoice_totals pričakuje XML root (_Element)
                    totals = parse_invoice_totals(
                        LET.parse(invoice_path).getroot()
                    )
                    header_total = totals.get("net") or Decimal("0")
                    _ = totals.get("doc_discount", Decimal("0"))
                    gross = totals.get("gross") or (
                        totals.get("net", Decimal("0"))
                        + totals.get("vat", Decimal("0"))
                    )
                    _t("keep_lines=1 rows=%d", len(df))
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "GUI fallback to analyze_invoice (reason: %s)", exc
                    )
                    df, header_total, _ = analyze_invoice(
                        str(invoice_path), str(suppliers)
                    )
                    gross = extract_grand_total(invoice_path)
            else:
                df, header_total, _ = analyze_invoice(
                    str(invoice_path), str(suppliers)
                )
                gross = extract_grand_total(invoice_path)
                _t("keep_lines=0 rows=%d", len(df))

            if "rabata" in df.columns:
                df["rabata"] = df["rabata"].fillna(Decimal("0"))
            else:
                df["rabata"] = Decimal("0")

        elif invoice_path.suffix.lower() == ".pdf":
            df = parse_pdf(str(invoice_path))
            if "rabata" not in df.columns:
                df["rabata"] = Decimal("0")
            header_total = df["vrednost"].sum()
            gross = header_total
        else:
            messagebox.showerror(
                "Napaka", f"Nepodprta datoteka: {invoice_path}"
            )
            return
    except Exception as exc:
        messagebox.showerror("Napaka", str(exc))
        return

    from wsm.utils import main_supplier_code

    supplier_code = main_supplier_code(df) or "unknown"
    sup_map = _load_supplier_map(Path(suppliers))
    map_vat = sup_map.get(supplier_code, {}).get("vat") if sup_map else None
    vat = map_vat
    # Če je koda še "unknown" in VAT obstaja, uporabi kar davčno številko
    if supplier_code == "unknown" and vat:
        supplier_code = vat

    info = sup_map.get(supplier_code, {})
    vat_id = vat or (info.get("vat") if isinstance(info, dict) else None)

    key = choose_supplier_key(vat_id, supplier_code)
    base_dir = Path(suppliers)
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        messagebox.showerror(
            "Napaka",
            f"Mapa {base_dir} ni dosegljiva oziroma je ni mogoče ustvariti.",
        )
        return
    if not key:
        messagebox.showwarning(
            "Opozorilo",
            "Davčna številka dobavitelja ni znana; mapa ne bo ustvarjena.",
        )
        links_dir = base_dir
    else:
        key_safe = sanitize_folder_name(key)
        links_dir = base_dir / key_safe
        links_dir.mkdir(parents=True, exist_ok=True)

    if (links_dir / f"{supplier_code}_povezane.xlsx").exists():
        links_file = links_dir / f"{supplier_code}_povezane.xlsx"
    else:
        links_file = (
            links_dir / f"{supplier_code}_{links_dir.name}_povezane.xlsx"
        )

    sifre_file = wsm_codes
    if sifre_file.exists():
        try:
            wsm_df = load_catalog(sifre_file)
            logging.info(
                "Catalog %s loaded: %d rows, columns=%s",
                sifre_file,
                len(wsm_df),
                sorted(wsm_df.columns),
            )
            missing = {"wsm_sifra", "wsm_naziv"} - set(wsm_df.columns)
            if missing:
                msg = (
                    f"Manjkajoči stolpci {missing}. "
                    f"Najdeni: {list(wsm_df.columns)}"
                )
                raise ValueError(msg)
        except Exception as exc:
            logging.warning(f"Napaka pri branju {sifre_file}: {exc}")
            wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])
    else:
        logging.warning(f"Datoteka {sifre_file} ne obstaja.")
        wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    kw_file = Path(keywords)
    if kw_file.exists():
        try:
            if kw_file.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
                kw_df = pd.read_excel(kw_file, dtype=str)
            else:
                kw_df = pd.read_csv(kw_file, dtype=str)
            kw_df = _rename_with_aliases(kw_df, KEYWORD_ALIAS_MAP)
            logging.info(
                "Keywords %s loaded: %d rows, columns=%s",
                kw_file,
                len(kw_df),
                sorted(kw_df.columns),
            )
            if not {"wsm_sifra", "keyword"} <= set(kw_df.columns):
                msg = (
                    "Manjkajoči stolpci v ključnih besedah. "
                    f"Najdeni: {list(kw_df.columns)}"
                )
                raise ValueError(msg)
            _ = load_keywords_map(kw_file)
        except Exception as exc:
            logging.warning(f"Napaka pri branju {kw_file}: {exc}")
    else:
        logging.warning(f"Datoteka {kw_file} ne obstaja.")

    try:
        from wsm.utils import povezi_z_wsm

        df = povezi_z_wsm(
            df, str(sifre_file), str(keywords), suppliers, supplier_code
        )
    except Exception as exc:
        logging.warning(f"Napaka pri samodejnem povezovanju: {exc}")

    review_links(
        df, wsm_df, links_file, header_total, invoice_path, invoice_gross=gross
    )
