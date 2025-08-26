# File: wsm/ui/review/gui.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Tuple, Optional

import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import builtins
from lxml import etree as LET
import os

from wsm.utils import short_supplier_name, _clean, _build_header_totals
from wsm.constants import PRICE_DIFF_THRESHOLD
from wsm.parsing.eslog import get_supplier_info, XML_PARSER
from wsm.supplier_store import _norm_vat
from wsm.ui.review.helpers import (
    ensure_eff_discount_col,
    first_existing_series,
)
from .helpers import (
    _fmt,
    _norm_unit,
    _merge_same_items,
    _apply_price_warning,
)
from .io import _save_and_close, _load_supplier_map
from .summary_columns import SUMMARY_COLS, SUMMARY_KEYS, SUMMARY_HEADS
from .summary_utils import vectorized_discount_pct, summary_df_from_records

builtins.tk = tk
builtins.simpledialog = simpledialog

# Feature flag controlling whether editing starts only after pressing Enter
EDIT_ON_ENTER = os.getenv("WSM_EDIT_ON_ENTER", "1") not in {
    "0",
    "false",
    "False",
}

# Feature flag controlling whether items are grouped by discount/price
GROUP_BY_DISCOUNT = os.getenv("WSM_GROUP_BY_DISCOUNT", "1") not in {
    "0",
    "false",
    "False",
}

# Should the summary include only booked items? (default NO)
ONLY_BOOKED_IN_SUMMARY = os.getenv("WSM_SUMMARY_ONLY_BOOKED", "0") not in {
    "0",
    "false",
    "False",
}

DEC2 = Decimal("0.01")
DEC_PCT_MIN = Decimal("-100")
DEC_PCT_MAX = Decimal("100")

EXCLUDED_CODES = {"UNKNOWN", "OSTALO", "OTHER", "NAN"}


def _booked_mask_from(df_or_sr: pd.DataFrame | pd.Series) -> pd.Series:
    """True, če je vrstica KNJIŽENA (ima smiselno wsm_sifra)."""
    if isinstance(df_or_sr, pd.Series):
        sr = df_or_sr
    else:
        # Primarno uporabljaj grid-stolpec; display kopija je lahko
        # nesinhronizirana
        col = first_existing_series(df_or_sr, ["wsm_sifra", "WSM šifra"])
        if col is None:
            return pd.Series(False, index=df_or_sr.index)
        sr = col
    _ws = sr.fillna("").astype(str).str.strip()
    _wsU = _ws.str.upper()
    return (_ws != "") & (~_wsU.isin(EXCLUDED_CODES))


def _safe_pct(v) -> Optional[Decimal]:
    try:
        d = _to_dec(v)
    except Exception:  # pragma: no cover - defensive
        return None
    if d < DEC_PCT_MIN or d > DEC_PCT_MAX:
        return None
    return d


def _to_dec(v: object) -> Decimal:
    """Best-effort conversion to :class:`Decimal`."""
    try:
        if isinstance(v, Decimal):
            return v
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except Exception:  # pragma: no cover - defensive
        return Decimal("0")


def _discount_bucket(row: dict) -> Tuple[Decimal, Decimal]:
    """
    Vrni podpis rabata: (rabat_%, enotna_neto_po_rabatu).
    – rabat% zaokrožimo na 2 dec,
    – enotno ceno po rabatu na 3 dec.
    Robustno poiščemo stolpce tudi z GUI imeni:
    'Net. pred rab.', 'Net. po rab.'.
    Če je mogoče, unit_after preračunamo iz Skupna neto / Količina.
    """
    # 1) najprej išči izračunan efektivni rabat; šele nato surove vrednosti
    rab_keys = ("eff_discount_pct", "rabata_pct", "Rabat (%)", "rabat_pct")
    before_keys = (
        "cena_pred_rabatom",
        "net_pred_rab",
        "unit_net_before",
        "Net. pred rab.",
        "Net. pred rab",
    )
    after_keys = (
        "cena_po_rabatu",
        "net_po_rab",
        "unit_net_after",
        "unit_price_net",
        "Net. po rabatu",
        "Net. po rab.",
        "Net. po rab",
    )
    # pomembno: raje uporabi normalizirano količino, če obstaja
    qty_keys = ("kolicina_norm", "Količina", "kolicina")
    total_net_keys = ("Skupna neto", "vrednost", "Neto po rabatu", "total_net")

    pct = None
    for k in rab_keys:
        if k in row:
            pct = _safe_pct(row.get(k))
            if pct is not None:
                break

    unit_before = None
    for k in before_keys:
        if k in row and row.get(k) not in (None, ""):
            unit_before = _to_dec(row.get(k))
            break

    unit_after = None
    for k in after_keys:
        if k in row and row.get(k) not in (None, ""):
            unit_after = _to_dec(row.get(k))
            break

    # ❶ Poskusi bolj zanesljivo izračunati enotno ceno po rabatu iz total/qty
    qty = None
    for k in qty_keys:
        if k in row and row.get(k) not in (None, ""):
            qty = _to_dec(row.get(k))
            break
    total_net = None
    for k in total_net_keys:
        if k in row and row.get(k) not in (None, ""):
            total_net = _to_dec(row.get(k))
            break
    if qty and qty > 0 and total_net is not None:
        ua_calc = total_net / qty
        # Uporabi izračunano enotno ceno, če unit_after manjka ali
        # očitno ne ustreza
        if (
            unit_after is None
            or unit_after == 0
            or (ua_calc - (unit_after or 0)).copy_abs() > Decimal("0.00005")
        ):
            unit_after = ua_calc

    # če % še vedno nimamo, ga izračunaj iz unit_before/after
    if (
        pct is None
        and unit_before
        and unit_before > 0
        and unit_after is not None
    ):
        pct = (Decimal("1") - (unit_after / unit_before)) * Decimal("100")

    if pct is None:
        pct = Decimal("0")

    pct = pct.quantize(DEC2, rounding=ROUND_HALF_UP)
    # manj občutljivo na drobne razlike: 3 decimalke
    ua3 = (unit_after if unit_after is not None else Decimal("0")).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )
    return (pct, ua3)


# Logger setup
log = logging.getLogger(__name__)
TRACE = os.getenv("WSM_TRACE", "0") not in {"0", "false", "False"}
if TRACE:
    logging.getLogger().setLevel(logging.DEBUG)


def _t(msg, *args):
    if TRACE:
        log.warning("[TRACE GUI] " + msg, *args)


# Lepo formatirano opozorilo za grid
def _format_opozorilo(row: pd.Series) -> str:
    try:
        if bool(row.get("is_gratis")):
            unit = None
            db = row.get("_discount_bucket")
            if isinstance(db, (tuple, list)) and len(db) == 2:
                unit = db[1]
            if unit is None:
                unit = row.get("cena_po_rabatu", "0")
            unit = _as_dec(unit, "0").quantize(
                Decimal("0.0000"), rounding=ROUND_HALF_UP
            )
            return f"rabat 100.00% @ {unit} - GRATIS"
        # uporabi efektivni rabat, če ga imamo; sicer standardnega
        # (negativno ničlo sproti počistimo)
        pct = row.get("rabata_pct", row.get("eff_discount_pct", Decimal("0")))
        if not isinstance(pct, Decimal):
            try:
                import pandas as pd

                if pd.isna(pct):
                    pct = Decimal("0")
                else:
                    pct = Decimal(str(pct))
            except Exception:
                pct = Decimal(str(pct or "0"))
        unit = None
        db = row.get("_discount_bucket")
        if isinstance(db, (tuple, list)) and len(db) == 2:
            unit = db[1]
        if unit is None:
            unit = row.get("cena_po_rabatu", 0)
        unit = _as_dec(unit, default="0").quantize(
            Decimal("0.0000"), rounding=ROUND_HALF_UP
        )
        pct = _clean_neg_zero(pct).quantize(DEC2, rounding=ROUND_HALF_UP)
        return f"rabat {pct}% @ {unit}"
    except Exception:
        return ""


# --- robust Decimal coercion (prevents InvalidOperation on NaN/None/strings)
def _as_dec(x, default: str = "0") -> Decimal:
    """
    Convert value to Decimal safely.
    Any NaN/None/empty/invalid → Decimal(default).
    Also normalizes comma decimals.
    """
    try:
        if isinstance(x, Decimal):
            return x if x.is_finite() else Decimal(default)
        # pandas/numpy NaN or None
        try:
            import pandas as pd  # local import to avoid hard dep

            if x is None or pd.isna(x):
                return Decimal(default)
        except Exception:
            if x is None:
                return Decimal(default)
        s = str(x).strip()
        if not s:
            return Decimal(default)
        s = s.replace(",", ".")
        d = Decimal(s)
        return d if d.is_finite() else Decimal(default)
    except Exception:
        return Decimal(default)


def _clean_neg_zero(val):
    """Normalize Decimal('-0') or -0.00 to plain zero."""
    d = _as_dec(val, default="0")
    return d if d != 0 else _as_dec("0", default="0")


_CURRENT_GRID_DF: pd.DataFrame | None = None


def _apply_multiplier(
    df: pd.DataFrame,
    idx: int,
    multiplier: Decimal,
    tree: ttk.Treeview | None = None,
    update_summary: Callable | None = None,
    update_totals: Callable | None = None,
) -> None:
    """Apply a quantity multiplier to a DataFrame row.

    The quantity is multiplied by ``multiplier`` while unit prices are divided
    by the same value so the line total remains unchanged. When a Treeview and
    update callbacks are provided, the visual row and aggregated totals are
    refreshed as well.

    Parameters
    ----------
    df:
        DataFrame containing invoice lines.
    idx:
        Index of the row to update.
    multiplier:
        Factor by which to multiply the normalized quantity.
    tree:
        Optional ``ttk.Treeview`` showing the invoice lines.
    update_summary:
        Callback to refresh the summary Treeview.
    update_totals:
        Callback to refresh aggregated totals.
    """
    if not isinstance(multiplier, Decimal):
        multiplier = Decimal(str(multiplier))

    old_qty = df.at[idx, "kolicina_norm"]
    old_price = df.at[idx, "cena_po_rabatu"]

    if "multiplier" not in df.columns:
        df["multiplier"] = Decimal("1")
    try:
        current = df.at[idx, "multiplier"]
    except Exception:
        current = Decimal("1")
    if not isinstance(current, Decimal):
        try:
            current = Decimal(str(current))
        except Exception:
            current = Decimal("1")
    df.at[idx, "multiplier"] = current * multiplier

    df.at[idx, "kolicina_norm"] *= multiplier
    df.at[idx, "cena_po_rabatu"] /= multiplier
    df.at[idx, "cena_pred_rabatom"] /= multiplier
    df.at[idx, "total_net"] = (
        df.at[idx, "kolicina_norm"] * df.at[idx, "cena_po_rabatu"]
    )

    log.debug(
        "Applied multiplier %s to row %s: quantity %s -> %s, price %s -> %s",
        multiplier,
        idx,
        old_qty,
        df.at[idx, "kolicina_norm"],
        old_price,
        df.at[idx, "cena_po_rabatu"],
    )

    if tree is not None:
        row_id = str(idx)
        tree.set(row_id, "kolicina_norm", _fmt(df.at[idx, "kolicina_norm"]))
        tree.set(
            row_id, "cena_pred_rabatom", _fmt(df.at[idx, "cena_pred_rabatom"])
        )
        tree.set(row_id, "cena_po_rabatu", _fmt(df.at[idx, "cena_po_rabatu"]))
        tree.set(row_id, "total_net", _fmt(df.at[idx, "total_net"]))

    if update_summary:
        update_summary()
    if update_totals:
        update_totals()


def review_links(
    df: pd.DataFrame,
    wsm_df: pd.DataFrame,
    links_file: Path,
    invoice_total: Decimal,
    invoice_path: Path | None = None,
    price_warn_pct: float | int | Decimal | None = None,
    invoice_gross: Decimal | None = None,
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
    invoice_gross : decimal.Decimal, optional
        Invoice grand total used when the XML is not available for extracting
        header amounts.

    Returns
    -------
    pandas.DataFrame
        The reviewed invoice lines including any document-level correction
        rows.
    """

    # Prepreči UnboundLocalError za 'pd' in 'Decimal' zaradi poznejših lokalnih
    # importov v tej funkciji ter poskrbi za Decimal util.
    import pandas as pd
    from decimal import Decimal, ROUND_HALF_UP

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
    supplier_code: str = "Unknown"
    # Try to extract supplier code directly from the invoice XML
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            tree = LET.parse(invoice_path, parser=XML_PARSER)
            supplier_code_raw = get_supplier_info(tree)
            supplier_code_norm = _norm_vat(supplier_code_raw)
            if supplier_code_norm and supplier_code_raw.upper().startswith(
                "SI"
            ):
                supplier_code = supplier_code_norm
            else:
                supplier_code = supplier_code_raw
            log.info("Supplier code extracted: %s", supplier_code)
        except Exception as exc:
            log.debug("Supplier code lookup failed: %s", exc)
    suppliers_file = links_file.parent.parent
    log.debug(f"Pot do mape links: {suppliers_file}")
    sup_map = _load_supplier_map(suppliers_file)

    log.info("Resolved supplier code: %s", supplier_code)
    supplier_info = sup_map.get(supplier_code, {})
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

    full_supplier_name = supplier_info.get("ime") or inv_name or supplier_code
    supplier_name = short_supplier_name(full_supplier_name)

    log.info(f"Default name retrieved: {supplier_name}")
    log.debug(f"Supplier info: {supplier_info}")

    header_totals = _build_header_totals(
        invoice_path, invoice_total, invoice_gross
    )

    service_date = (
        header_totals.get("service_date")
        or supplier_info.get("service_date")
        or ""
    )

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

    old_multiplier_dict = {}
    if "multiplier" in manual_old.columns:
        old_multiplier_dict = (
            manual_old.set_index(["sifra_dobavitelja", "naziv_ckey"])[
                "multiplier"
            ]
            .apply(lambda x: Decimal(str(x)) if pd.notna(x) else Decimal("1"))
            .to_dict()
        )

    df["naziv_ckey"] = df["naziv"].map(_clean)
    booked_keys = {
        (str(s), ck)
        for s, ck, ws in manual_old[
            ["sifra_dobavitelja", "naziv_ckey", "wsm_sifra"]
        ].itertuples(index=False)
        if pd.notna(ws) and str(ws).strip()
    }
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
    df["multiplier"] = Decimal("1")
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
    df["rabata_pct"] = vectorized_discount_pct(
        df["vrednost"] + df["rabata"], df["vrednost"]
    )
    df["total_net"] = df["vrednost"]
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
    if old_multiplier_dict:
        non_default = {
            k: v for k, v in old_multiplier_dict.items() if v != Decimal("1")
        }
        if non_default:
            log.info("Applying multipliers for %d rows", len(non_default))
            log.debug("Multiplier mapping: %s", non_default)
        for idx, row in df.iterrows():
            key = (row["sifra_dobavitelja"], row["naziv_ckey"])
            mult = old_multiplier_dict.get(key, Decimal("1"))
            if mult != Decimal("1"):
                _apply_multiplier(df, idx, mult)
    df["warning"] = pd.NA
    log.debug("df po normalizaciji: %s", df.head().to_dict())
    # Ensure 'multiplier' is a sane Decimal for later comparisons/UI
    if "multiplier" not in df.columns:
        df["multiplier"] = Decimal("1")
    else:
        df["multiplier"] = df["multiplier"].map(lambda v: _as_dec(v, "1"))
    # STEP0: surovi podatki
    try:
        cols_dbg = [
            c
            for c in (
                "sifra_dobavitelja",
                "naziv",
                "naziv_ckey",
                "enota",
                "enota_norm",
                "Količina",
                "kolicina_norm",
                "vrednost",
                "Skupna neto",
                "Neto po rabatu",
                "cena_po_rabatu",
                "rabata",
                "rabata_pct",
                "eff_discount_pct",
                "line_bucket",
                "is_gratis",
            )
            if c in df.columns
        ]
        _t("STEP0 rows=%d, cols=%d -> %s", len(df), len(df.columns), cols_dbg)
        _t("STEP0 head=%s", df[cols_dbg].head(10).to_dict("records"))
    except Exception:
        pass

    # (premaknjeno) opozorila bomo preračunali po združevanju

    # 1) obvezno: zagotovimo eff_discount_pct še pred merge
    df = ensure_eff_discount_col(df)
    _t(
        "STEP1 after ensure_eff_discount_pct: nulls=%s sample=%s",
        (
            df["eff_discount_pct"].isna().sum()
            if "eff_discount_pct" in df.columns
            else "n/a"
        ),
        (
            df[["eff_discount_pct"]].head(5).to_dict("records")
            if "eff_discount_pct" in df.columns
            else "n/a"
        ),
    )

    # Označi GRATIS vrstice (količina > 0 in neto = 0), da se ne izgubijo
    from wsm.ui.review.helpers import first_existing_series

    if "is_gratis" not in df.columns:
        df["is_gratis"] = False
    qty_s = first_existing_series(
        df, ["Količina", "kolicina_norm", "kolicina"]
    )
    total_s = first_existing_series(
        df, ["Skupna neto", "vrednost", "Neto po rabatu", "total_net"]
    )
    if qty_s is not None and total_s is not None:
        q = qty_s.map(
            lambda v: Decimal(str(v)) if v not in (None, "") else Decimal("0")
        )
        t = total_s.map(
            lambda v: Decimal(str(v)) if v not in (None, "") else Decimal("0")
        )
        df.loc[(q > 0) & (t == 0), "is_gratis"] = True
    _t(
        "STEP2 is_gratis count=%s",
        int(df["is_gratis"].sum()) if "is_gratis" in df.columns else "n/a",
    )

    # očisti morebitne nemogoče vrednosti rabata
    if "rabata_pct" in df.columns:

        def _clip(v):
            d = _safe_pct(v)
            if d is None:
                d = Decimal("0")
            return d.quantize(DEC2, rounding=ROUND_HALF_UP)

        df["rabata_pct"] = df["rabata_pct"].map(_clip)

    # 2) pripravimo 'discount bucket' za stabilno grupiranje
    if GROUP_BY_DISCOUNT:
        if "line_bucket" in df.columns:
            df["_discount_bucket"] = df["line_bucket"]
        else:
            df["_discount_bucket"] = df.apply(_discount_bucket, axis=1)

        # Sanacija: poskrbi, da je na VSAKI vrstici tuple (pct, unit)
        def _is_valid_bucket(val):
            return (
                isinstance(val, (tuple, list))
                and len(val) == 2
                and all(not pd.isna(x) for x in val)
            )

        def _coerce_bucket(row):
            val = row.get("_discount_bucket", None)
            if _is_valid_bucket(val):
                return tuple(val)
            # fallback iz trenutno vidnih polj (robustno)
            return _discount_bucket(row)

        df["_discount_bucket"] = df.apply(_coerce_bucket, axis=1)
        # nikoli ne dovoli implicitne pretvorbe v float (npr. zaradi NaN)
        df["_discount_bucket"] = df["_discount_bucket"].astype(object)

        try:
            bad = (
                df["_discount_bucket"]
                .apply(
                    lambda v: not (
                        isinstance(v, (tuple, list)) and len(v) == 2
                    )
                )
                .sum()
            )
            _t(
                "STEP3 bucket ready: rows=%d, invalid=%d, uniq=%s",
                len(df),
                int(bad),
                (
                    df["_discount_bucket"].nunique(dropna=False)
                    if "_discount_bucket" in df.columns
                    else "n/a"
                ),
            )
        except Exception:
            pass

        # Koliko unikatnih ključev (brez in z bucketom)
        try:
            base_cols = [
                c
                for c in (
                    "sifra_dobavitelja",
                    "naziv_ckey",
                    "enota_norm",
                )
                if c in df.columns
            ]
            base = (
                df[base_cols].drop_duplicates().shape[0]
                if base_cols
                else "n/a"
            )
            with_b = (
                df[base_cols + ["_discount_bucket"]].drop_duplicates().shape[0]
                if base_cols and "_discount_bucket" in df.columns
                else "n/a"
            )
            _t(
                "STEP3 unique groups base=%s, base+bucket=%s using %s",
                base,
                with_b,
                base_cols,
            )
        except Exception:
            pass

    if os.getenv("WSM_DEBUG_BUCKET") == "1":
        for i, r in df.iterrows():
            log.warning(
                "DBG key=(%s, %s, %s) eff=%s bucket=%s qty=%s "
                "total=%s gratis=%s",
                r.get("sifra_dobavitelja"),
                r.get("naziv_ckey"),
                r.get("enota_norm"),
                r.get("eff_discount_pct"),
                _discount_bucket(r),
                r.get("Količina")
                or r.get("kolicina_norm")
                or r.get("kolicina"),
                r.get("Skupna neto")
                or r.get("vrednost")
                or r.get("Neto po rabatu")
                or r.get("total_net"),
                r.get("is_gratis"),
            )

    # 3) šele zdaj združi enake postavke (ključ vključuje eff_discount_pct)
    _t("STEP4 call _merge_same_items on %d rows", len(df))
    # STEP4: združi iste artikle po bucketu/rabatu (GRATIS ostane ločeno)
    df = _merge_same_items(df)

    # --- Po MERGE: zagotovimo vse prikazne stolpce, ki jih GUI bere ---
    # 1) 'rabata_pct' – če ga ni, vzemi eff_discount_pct (ali 0)
    if "rabata_pct" not in df.columns:
        if "eff_discount_pct" in df.columns:
            df["rabata_pct"] = df["eff_discount_pct"].map(
                lambda v: Decimal(str(v or "0"))
            )
        else:
            df["rabata_pct"] = Decimal("0")
    else:
        # normaliziraj v Decimal
        df["rabata_pct"] = df["rabata_pct"].map(
            lambda v: Decimal(str(v or "0"))
        )

    # 2) 'cena_pred_rabatom' – enotna neto pred rabatom
    #    če ni na voljo, izračunaj iz 'cena_po_rabatu' in 'eff_discount_pct'
    if "cena_pred_rabatom" not in df.columns:
        if "cena_po_rabatu" in df.columns and "eff_discount_pct" in df.columns:

            def _unit_before(row):
                try:
                    ua = Decimal(str(row.get("cena_po_rabatu", "0") or "0"))
                    pct = Decimal(str(row.get("eff_discount_pct", "0") or "0"))
                    # ua / (1 - pct/100); pri 0% ali 100% fallback na ua
                    denom = Decimal("1") - (pct / Decimal("100"))
                    if denom == 0:
                        return ua.quantize(
                            Decimal("0.0001"), rounding=ROUND_HALF_UP
                        )
                    return (ua / denom).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                except Exception:
                    return Decimal("0")

            df["cena_pred_rabatom"] = df.apply(_unit_before, axis=1)
        else:
            # brez podatkov o rabatu – prikaži isto kot po rabatu
            base = (
                df["cena_po_rabatu"]
                if "cena_po_rabatu" in df.columns
                else Decimal("0")
            )
            df["cena_pred_rabatom"] = base

    # 3) 'Skupna neto' – če manjka, privzemi 'vrednost'
    if "Skupna neto" not in df.columns and "vrednost" in df.columns:
        df["Skupna neto"] = df["vrednost"]

    # -------------------------------------------------------------------
    # Efektivni rabat (upošteva gratis) – samo za prikaz v GUI
    try:
        # raje normalizirana količina (če obstaja)
        qty_col = next(
            (
                c
                for c in ("kolicina_norm", "Količina", "kolicina")
                if c in df.columns
            ),
            None,
        )
        tot_col = next(
            (
                c
                for c in (
                    "Skupna neto",
                    "vrednost",
                    "Neto po rabatu",
                    "total_net",
                )
                if c in df.columns
            ),
            None,
        )
        # Za izračun efektivnega rabata grupiramo:
        # - če je vklopljeno grupiranje po ceni -> tudi po _discount_bucket
        # - sicer samo po artiklu (brez bucketa)
        base_grp = [
            c
            for c in ("sifra_dobavitelja", "naziv_ckey", "enota_norm")
            if c in df.columns
        ]
        if GROUP_BY_DISCOUNT and "_discount_bucket" in df.columns:
            grp_cols = base_grp + ["_discount_bucket"]
        else:
            grp_cols = base_grp
        if qty_col and tot_col and grp_cols:

            def _unit_from_bucket(r: pd.Series) -> Decimal:
                b = r.get("_discount_bucket")
                if isinstance(b, (tuple, list)) and len(b) == 2:
                    return _as_dec(b[1], "0")
                return _as_dec(r.get("cena_po_rabatu", "0"), "0")

            def _unit_row_effective(r: pd.Series) -> Decimal:
                # raje izračun iz vsote/količine, če je možen (ujema enote)
                try:
                    q = _as_dec(r.get(qty_col, "0"), "0")
                    t = _as_dec(r.get(tot_col, "0"), "0")
                    if q and q > 0:
                        return t / q
                except Exception:
                    pass
                return _unit_from_bucket(r)

            def _calc_group(g: pd.DataFrame) -> pd.Series:
                # Vsota imenovalca po vrsticah: sum(unit_i * qty_i)
                qty_vals = g[qty_col].map(lambda v: _as_dec(v, "0"))
                denom = Decimal("0")
                for idx, q in qty_vals.items():
                    if q and q > 0:
                        u = _unit_row_effective(g.loc[idx])
                        denom += u * q
                paid_mask = ~g.get(
                    "is_gratis", pd.Series(False, index=g.index)
                ).fillna(False)
                paid_tot = sum(
                    (_as_dec(x, "0") for x in g.loc[paid_mask, tot_col]),
                    Decimal("0"),
                )
                if denom == 0:
                    eff = None
                else:
                    eff = (Decimal("1") - (paid_tot / denom)) * Decimal("100")
                    eff = eff.quantize(DEC2, rounding=ROUND_HALF_UP)
                return pd.Series({"_eff_pct_group": eff})

            try:
                eff_df = (
                    df.groupby(grp_cols, dropna=False)
                    .apply(_calc_group, include_groups=False)
                    .reset_index()
                )
            except TypeError:
                eff_df = (
                    df.groupby(grp_cols, dropna=False)
                    .apply(_calc_group)
                    .reset_index()
                )
            df = df.merge(eff_df, on=grp_cols, how="left")
            mask_paid = ~df.get(
                "is_gratis", pd.Series(False, index=df.index)
            ).fillna(False)
            # zapiši efektivni rabat, če ga imamo; sicer pusti obstoječega
            df.loc[mask_paid & df["_eff_pct_group"].notna(), "rabata_pct"] = (
                df["_eff_pct_group"]
            )
            df.drop(columns=["_eff_pct_group"], inplace=True)
    except Exception as exc:
        log.debug("Efektivni rabat (GUI) preskočen: %s", exc)

    # po merge + po effekt. rabatu: prikaži 100% za GRATIS vrstice
    if "is_gratis" in df.columns and "rabata_pct" in df.columns:
        df.loc[df["is_gratis"].fillna(False), "rabata_pct"] = Decimal("100")

    # Normaliziraj -0 na 0 v rabata_pct
    if "rabata_pct" in df.columns:
        df["rabata_pct"] = df["rabata_pct"].map(_clean_neg_zero)

    # Mini airbag: derive 'cena_po_rabatu' from '_discount_bucket' if missing
    if "cena_po_rabatu" not in df.columns and "_discount_bucket" in df.columns:

        def _from_bucket(row: pd.Series) -> Decimal:
            b = row.get("_discount_bucket")
            if isinstance(b, (tuple, list)) and len(b) == 2:
                return _as_dec(b[1], "0")
            return _as_dec(row.get("cena_po_rabatu", "0"), "0")

        df["cena_po_rabatu"] = df.apply(_from_bucket, axis=1)

    # Za prikaz dosledno zaokroži rabata_pct na 2 decimalni mesti
    # (ustvari stolpec, če manjka)
    if "rabata_pct" not in df.columns:
        df["rabata_pct"] = Decimal("0")
    df["rabata_pct"] = df["rabata_pct"].map(
        lambda v: _as_dec(v, "0").quantize(DEC2, rounding=ROUND_HALF_UP)
    )

    # -- po merge-u format opozorila (če obstaja)
    try:
        df["warning"] = df.apply(_format_opozorilo, axis=1)
    except Exception as exc:
        log.debug("warning format (post-merge) failed: %s", exc)

    def _price_from_bucket(row):
        b = row.get("_discount_bucket")
        if isinstance(b, (tuple, list)) and len(b) == 2:
            return _as_dec(b[1], "0")
        return _as_dec(row.get("cena_po_rabatu", "0"), "0")

    if GROUP_BY_DISCOUNT and "_discount_bucket" in df.columns:
        # zgolj kozmetika – izračun cene iz bucket-a,
        # dejanska teža je že v 'Skupna neto'
        df["cena_po_rabatu"] = df.apply(_price_from_bucket, axis=1)
    _t(
        "STEP5 after merge: rows=%d head=%s",
        len(df),
        df[
            [
                c
                for c in (
                    "naziv",
                    "enota_norm",
                    "Količina",
                    "Skupna neto",
                    "_discount_bucket",
                    "is_gratis",
                )
                if c in df.columns
            ]
        ]
        .head(10)
        .to_dict("records"),
    )

    # ------------------------------------------------------------------
    # BOOKING LOGIKA
    #  - Predlog (wsm_sifra) je le informativen.
    #  - "Dejansko knjiženje" (za grid in POVZETEK) držimo v _booked_sifra.
    #  - Privzeto je VSE pod "OSTALO".
    # ------------------------------------------------------------------
    def _init_booking_columns(df0: pd.DataFrame) -> None:
        if df0 is None or df0.empty:
            return

        # Predlog hranimo posebej (ne vpliva na povzetek)
        df0["_suggested_wsm_sifra"] = (
            df0["wsm_sifra"] if "wsm_sifra" in df0.columns else None
        )

        # Dejansko knjiženje – PRIVZETO SAMO "OSTALO"
        # (BREZ uporabe was_ever_booked / last_booked_sifra / booked_sifra)
        df0["_booked_sifra"] = "OSTALO"

        # Ključ za povzetek je vedno _booked_sifra
        df0["_summary_key"] = (
            df0["_booked_sifra"]
            .astype(object)
            .where(~pd.isna(df0["_booked_sifra"]), "OSTALO")
            .replace(
                {
                    None: "OSTALO",
                    "": "OSTALO",
                    "<NA>": "OSTALO",
                    "nan": "OSTALO",
                    "NaN": "OSTALO",
                }
            )
        )

        # Prikaz v gridu je vezan na dejansko knjiženje (_summary_key):
        #  - če je OSTALO: šifra prazna, naziv "ostalo"
        #  - sicer: šifra = _summary_key, naziv = wsm_naziv
        def _disp_sifra(r):
            k = str(r.get("_summary_key", "") or "")
            return "" if k == "OSTALO" else k

        def _disp_naziv(r):
            k = str(r.get("_summary_key", "") or "")
            if k == "OSTALO":
                return "ostalo"
            return str(r.get("wsm_naziv", "") or "")

        df0["WSM šifra"] = df0.apply(_disp_sifra, axis=1)
        df0["WSM naziv"] = df0.apply(_disp_naziv, axis=1)

    # počisti morebitne ostanke iz prejšnje seje
    df.drop(
        columns=["_booked_sifra", "_summary_key", "WSM šifra", "WSM naziv"],
        errors="ignore",
        inplace=True,
    )
    _init_booking_columns(df)

    # --- Povzetek po WSM šifri z varnim ključem "OSTALO" ---
    # Povzetek vedno temelji na _summary_key (tj. dejanskem knjiženju),
    # predlogi ne vplivajo na razporeditev v povzetku.
    try:
        sum_col = next(
            c
            for c in ("Skupna neto", "total_net", "vrednost")
            if c in df.columns
        )
    except StopIteration:
        sum_col = None

    if sum_col:
        # vedno uporabljaj sveže izračunan _summary_key
        summary_key_col = "_summary_key"

        # povzetek po ključu
        summary = (
            df.groupby(summary_key_col, dropna=False)[sum_col]
            .sum()
            .reset_index()
        )
        # "OSTALO" postavimo na konec (kozmetika)
        summary["_is_ostalo"] = (
            summary[summary_key_col].astype(str).eq("OSTALO")
        )
        summary = summary.sort_values(
            by=["_is_ostalo", sum_col], ascending=[True, False]
        ).drop(columns="_is_ostalo")

        # logiraj povzetek (Decimal-varno)
        for _, r in summary.iterrows():
            label = str(r[summary_key_col])
            log.info("SUMMARY[%s] cena=%s", label, r[sum_col])

    total_s = first_existing_series(
        df, ["total_net", "Neto po rabatu", "vrednost", "Skupna neto"]
    )
    if total_s is None:
        total_s = pd.Series([Decimal("0")] * len(df))
    net_total = (
        total_s.map(
            lambda v: Decimal(str(v)) if v not in (None, "") else Decimal("0")
        )
        .sum()
        .quantize(Decimal("0.01"))
    )

    # 3) shrani grid za povzetek
    global _CURRENT_GRID_DF
    _CURRENT_GRID_DF = df

    base_root = tk._default_root
    if base_root is not None:
        root = tk.Toplevel(base_root)
        is_toplevel = True
    else:
        root = tk.Tk()
        is_toplevel = False

    # Window title shows the full supplier name while the on-screen
    # header can be a bit shorter for readability.
    root.title(f"Ročna revizija – {full_supplier_name}")
    root.supplier_name = full_supplier_name
    root.supplier_code = supplier_code
    root.service_date = service_date

    closing = False
    _after_totals_id: str | None = None
    bindings: list[tuple[tk.Misc, str]] = []
    header_after_id: str | None = None
    price_tip: tk.Toplevel | None = None
    last_warn_item: str | None = None

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
    date_var.set(service_date or "")
    invoice_var = tk.StringVar()

    def _refresh_header():
        parts_full = [full_supplier_name]
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
        supplier_var.set(full_supplier_name)
        header_var.set(" – ".join(parts_display))
        root.title(f"Ročna revizija – {' – '.join(parts_full)}")
        log.debug(
            f"_refresh_header: supplier_var={supplier_var.get()}, "
            f"date_var={date_var.get()}, invoice_var={invoice_var.get()}"
        )

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

    tk.Label(info_frame, text=full_supplier_name).grid(
        row=0, column=0, columnspan=3, sticky="w"
    )

    def _copy_to_clipboard(val: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(val)

    def _copy_supplier():
        text = (root.supplier_name or root.supplier_code or "").strip()
        if text:
            _copy_to_clipboard(text)

    def _copy_service_date():
        dt = (root.service_date or "").strip()
        if dt:
            _copy_to_clipboard(dt)

    tk.Button(
        info_frame,
        text="Kopiraj dobavitelja",
        command=_copy_supplier,
    ).grid(row=1, column=0, sticky="w", padx=(0, 4))
    tk.Button(
        info_frame,
        text="Kopiraj datum storitve",
        command=_copy_service_date,
    ).grid(row=1, column=1, sticky="w", padx=(0, 4))

    def copy_invoice_number() -> None:
        _copy_to_clipboard(invoice_var.get())

    tk.Button(
        info_frame,
        text="Kopiraj številko računa",
        command=copy_invoice_number,
    ).grid(row=1, column=2, sticky="w", padx=(0, 4))

    # Refresh header once widgets exist. ``after_idle`` ensures widgets are
    # fully initialized before values are set so the entries show up
    header_after_id = root.after_idle(_refresh_header)
    log.debug(
        f"after_idle scheduled: supplier_var={supplier_var.get()}, "
        f"date_var={date_var.get()}, invoice_var={invoice_var.get()}"
    )

    # totals_frame and individual total labels have been removed in favor of
    # displaying aggregated totals only within ``total_frame``.

    # Allow Escape to restore the original window size
    root.bind("<Escape>", lambda e: root.state("normal"))
    bindings.append((root, "<Escape>"))

    # Mapiraj 'Skupna neto' -> 'total_net', če je to potrebno
    if "total_net" not in df.columns and "Skupna neto" in df.columns:
        df["total_net"] = df["Skupna neto"]

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
        "WSM šifra",
        "WSM naziv",
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
        "WSM šifra",  # prikažemo dejansko knjiženje (OSTALO ali šifra)
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
    tree.tag_configure("unbooked", background="lightpink")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    if EDIT_ON_ENTER:
        try:
            tree.unbind("<Key>")
        except Exception:
            pass

    # Indikator "nikoli knjiženo" (za rdeče barvanje):
    #  - če obstaja was_ever_booked => True pomeni da JE bilo kdaj knjiženo
    #  - če obstaja le was_ever_linked, ga ne štejemo kot knjiženje
    if "_never_booked" not in df.columns:
        if "was_ever_booked" in df.columns:
            df["_never_booked"] = ~df["was_ever_booked"].astype(bool)
        else:
            # brez zgodovine ne ugibamo -> ne barvamo rdeče
            df["_never_booked"] = False

    # (opombo: barvanje izvedemo pri vsakem insertu vrstice spodaj)

    # --------------------------------------------------------
    # Urejanje: ENTER/F2 za začetek; po potrditvi NI kurzorja
    # (brez auto-typing ob navigaciji po Treeview)
    # --------------------------------------------------------

    # Guard flag: med programskim premikom selekcije
    # začasno blokiramo auto-edit
    _suspend_auto_edit = {"val": False}
    _block_next_begin = {"val": False}

    def _guard_select(evt: tk.Event):
        # blokiraj vse selekcijske side-effekte, ko program premika selekcijo
        # ali ko smo pravkar zaključili vnos in nočemo auto-edita
        if _suspend_auto_edit["val"] or _block_next_begin["val"]:
            return "break"

    # Vstavi "guard" bindtag pred obstoječe bindtage, da se izvede prej
    _GUARD_TAG = "TreeviewGuard"
    tree.bind_class(_GUARD_TAG, "<<TreeviewSelect>>", _guard_select, add="+")
    tree.bindtags((_GUARD_TAG, *tree.bindtags()))

    _active_editor = {"widget": None}

    def _remember_editor(evt: tk.Event):
        # Vsakokrat ko Entry/Combobox dobi fokus, si ga zapomnimo
        _active_editor["widget"] = evt.widget

    root.bind_class("Entry", "<FocusIn>", _remember_editor, add="+")
    root.bind_class("TEntry", "<FocusIn>", _remember_editor, add="+")
    root.bind_class("Combobox", "<FocusIn>", _remember_editor, add="+")
    root.bind_class("TCombobox", "<FocusIn>", _remember_editor, add="+")

    def _move_selection(delta: int = 1):
        cur = tree.focus()
        if not cur:
            children = tree.get_children("")
            if children:
                tree.focus(children[0])
                tree.selection_set(children[0])
            return
        kids = tree.get_children("")
        if cur in kids:
            i = kids.index(cur) + delta
            i = max(0, min(i, len(kids) - 1))
            nxt = kids[i]
            tree.focus(nxt)
            # začasno blokiraj auto-edit, dokler ne končamo premika
            _suspend_auto_edit["val"] = True
            try:
                tree.selection_set(nxt)
            finally:
                root.after_idle(
                    lambda: _suspend_auto_edit.__setitem__("val", False)
                )
            tree.see(nxt)

    def _finish_edit_and_move_next(evt: tk.Event | None = None):
        """
        Commit + zapri editor + premakni se navzdol,
        na naslednji vrstici pa naj se editor NE odpre sam.
        """
        try:
            # ➤ vklopi blokado TAKOJ (pred commit),
            #   da ujamemo morebitni "ostanek" Enterja
            _block_next_begin["val"] = True

            w = _active_editor.get("widget")
            if w and w.winfo_exists():
                try:
                    w.event_generate("<<Commit>>")
                except Exception:
                    pass
                try:
                    _update_summary()
                except Exception as e:
                    log.warning("Povzetka ni bilo mogoče osvežiti: %s", e)
                try:
                    w.destroy()
                except Exception:
                    pass

            tree.focus_set()  # brez kurzorja v polju
        except Exception:
            pass
        finally:
            _active_editor["widget"] = None

        _move_selection(+1)

        # utrdi fokus na tree in po malo daljšem času spusti blokado
        try:
            tree.focus_set()
            root.after(
                220, lambda: _block_next_begin.__setitem__("val", False)
            )
        except Exception:
            _block_next_begin["val"] = False
        return "break"

    # Potrditev edita: Enter / KP_Enter + preklic z Esc
    root.bind_class("Entry", "<Return>", _finish_edit_and_move_next, add="+")
    root.bind_class("TEntry", "<Return>", _finish_edit_and_move_next, add="+")
    root.bind_class("Entry", "<KP_Enter>", _finish_edit_and_move_next, add="+")
    root.bind_class(
        "TEntry", "<KP_Enter>", _finish_edit_and_move_next, add="+"
    )
    root.bind_class(
        "Entry", "<Escape>", lambda e: (tree.focus_set(), "break"), add="+"
    )
    root.bind_class(
        "TEntry", "<Escape>", lambda e: (tree.focus_set(), "break"), add="+"
    )

    root.bind_class(
        "Combobox", "<Return>", _finish_edit_and_move_next, add="+"
    )
    root.bind_class(
        "TCombobox", "<Return>", _finish_edit_and_move_next, add="+"
    )
    root.bind_class(
        "Combobox", "<KP_Enter>", _finish_edit_and_move_next, add="+"
    )
    root.bind_class(
        "TCombobox", "<KP_Enter>", _finish_edit_and_move_next, add="+"
    )
    root.bind_class(
        "Combobox", "<Escape>", lambda e: (tree.focus_set(), "break"), add="+"
    )
    root.bind_class(
        "TCombobox", "<Escape>", lambda e: (tree.focus_set(), "break"), add="+"
    )
    root.bind_class(
        "TCombobox",
        "<<ComboboxSelected>>",
        _finish_edit_and_move_next,
        add="+",
    )

    # V Treeviewu blokiraj tipkanje (da se edit ne začne sam od sebe).
    # Urejanje dovoli samo z Enter/KP_Enter/F2 (tvoj obstoječi handler).
    def _tree_keypress_guard(evt: tk.Event):
        if evt.keysym in ("Return", "KP_Enter", "F2"):
            return  # pusti obstoječim handlerjem
        ch = getattr(evt, "char", "") or ""
        if ch and ch.isprintable():
            return "break"  # blokiraj direktno tipkanje v grid

    tree.bind("<Key>", _tree_keypress_guard, add="+")

    # Eksplicitni začetek urejanja: ENTER / KP_Enter / F2 na izbrani vrstici
    def _begin_edit_current(evt=None):
        # če smo ravno zaključili prejšnji vnos, ignoriraj sprožitev
        if _block_next_begin["val"]:
            return "break"
        # če smo že v editorju, ne začenjaj znova
        try:
            w = _active_editor.get("widget")
            if w and w.winfo_exists():
                return "break"
        except Exception:
            pass
        # poskusi uporabiti obstoječo logiko za začetek edita
        # (npr. double-click handler)
        try:
            tree.event_generate("<Double-1>")
        except Exception:
            pass
        return "break"

    tree.bind("<Return>", _begin_edit_current, add="+")
    tree.bind("<KP_Enter>", _begin_edit_current, add="+")
    tree.bind("<F2>", _begin_edit_current, add="+")

    def _squelch_return_keypress_if_blocked(evt):
        if _block_next_begin["val"]:
            return "break"

    tree.bind(
        "<KeyPress-Return>", _squelch_return_keypress_if_blocked, add="+"
    )
    tree.bind(
        "<KeyPress-KP_Enter>", _squelch_return_keypress_if_blocked, add="+"
    )

    def _eat_return_release(evt):
        # po commit-u in premiku selekcije ne sme nič odpreti editorja
        return "break"

    tree.bind("<KeyRelease-Return>", _eat_return_release, add="+")
    tree.bind("<KeyRelease-KP_Enter>", _eat_return_release, add="+")

    # Če editor izgubi fokus (klik nekam drugam), zapri editor in skrij kurzor
    def _editor_focus_out(evt):
        """
        Če fokus prehaja na drug editor-like widget
        (npr. dropdown pri Comboboxu),
        ne jemlji fokusa – sicer pa fokus vrni na tree
        in počisti aktivni editor.
        """
        try:
            nf = root.focus_get()
            cls = ""
            if nf:
                try:
                    cls = nf.winfo_class()
                except Exception:
                    cls = ""
            # Pusti FokusOut pri prehodu na druge "editor" komponente
            # (vključno s Combobox dropdown)
            if cls in (
                "Entry",
                "TEntry",
                "Combobox",
                "TCombobox",
                "Listbox",
                "TComboboxPopdown",
            ):
                return
        except Exception:
            pass
        try:
            tree.focus_set()
        except Exception:
            pass
        _active_editor["widget"] = None

    root.bind_class("Entry", "<FocusOut>", _editor_focus_out, add="+")
    root.bind_class("TEntry", "<FocusOut>", _editor_focus_out, add="+")
    root.bind_class("Combobox", "<FocusOut>", _editor_focus_out, add="+")
    root.bind_class("TCombobox", "<FocusOut>", _editor_focus_out, add="+")

    for c, h in zip(cols, heads):
        tree.heading(c, text=h)
        width = (
            300
            if c == "naziv"
            else 80 if c == "enota_norm" else 160 if c == "warning" else 120
        )
        tree.column(c, width=width, anchor="w")

    def _safe_get(row, col, default=""):
        try:
            return row.get(col, default)
        except Exception:
            return default

    booked_keys = locals().get("booked_keys", set())
    for i, row in df.iterrows():
        vals = []
        for c in cols:
            v = _safe_get(row, c)
            # normaliziraj številke (odpravi -0, NaN) - bool ni število
            if (
                isinstance(v, Decimal)
                or isinstance(v, float)
                or (isinstance(v, int) and not isinstance(v, bool))
            ):
                v = _clean_neg_zero(v)
                vals.append(_fmt(v))
            else:
                if v is None or (hasattr(pd, "isna") and pd.isna(v)):
                    vals.append("")
                else:
                    vals.append(str(v))
        # obstoječa logika za določanje tagov (price_warn/gratis/linked/...)
        row_tags: list[str] = []
        key = (str(row.get("sifra_dobavitelja")), row.get("naziv_ckey"))
        if bool(row.get("_never_booked", False)) and key not in booked_keys:
            row_tags.append("unbooked")

        tree.insert("", "end", iid=str(i), values=vals, tags=tuple(row_tags))
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
        # združi tag-e in uredi po prioriteti: gratis > unbooked > price_warn
        existing = tree.item(str(i), "tags") or ()
        tags = set(existing)
        if warn:
            tags.add("price_warn")
        else:
            tags.discard("price_warn")
        ordered: list[str] = []
        for t in ("gratis", "unbooked", "price_warn"):
            if t in tags:
                ordered.append(t)
                tags.remove(t)
        ordered.extend(sorted(tags))  # ostali tagi brez posebne prioritete
        tree.item(str(i), tags=tuple(ordered))
        df.at[i, "warning"] = tooltip
        if GROUP_BY_DISCOUNT and "_discount_bucket" in df.columns:
            val = df.at[i, "_discount_bucket"]
            if _is_valid_bucket(val):
                pct, ua = val
            else:
                # Fallback, če je karkoli ušlo (npr. NaN)
                pct, ua = _discount_bucket(row)
            tag = f"rabat {pct}% @ {ua}"
            warn_existing = df.at[i, "warning"]
            if warn_existing is None or pd.isna(warn_existing):
                warn_existing = ""
            else:
                warn_existing = str(warn_existing)
            df.at[i, "warning"] = (
                (warn_existing + " · ") if warn_existing else ""
            ) + tag
            tree.set(str(i), "warning", df.at[i, "warning"])
        if "is_gratis" in row and row["is_gratis"]:
            current_tags = tree.item(str(i), "tags") or ()
            # odstrani morebitne obstoječe 'gratis',
            # potem ga postavi na začetek
            current_tags = tuple(t for t in current_tags if t != "gratis")
            #  ➜ 'gratis' naj bo PRVI, da barva vedno prime
            tree.item(str(i), tags=("gratis",) + current_tags)

            #  ➜ besedilo v stolpcu »Opozorilo«
            df.at[i, "warning"] = (
                (df.at[i, "warning"] + " · ") if df.at[i, "warning"] else ""
            ) + "GRATIS"
            tree.set(str(i), "warning", df.at[i, "warning"])
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

    # Column keys and headers derive from :mod:`summary_columns`
    # to stay in sync with :data:`SUMMARY_COLS` used throughout the project.
    summary_cols = SUMMARY_KEYS
    summary_heads = SUMMARY_HEADS
    assert SUMMARY_COLS == summary_heads
    summary_tree = ttk.Treeview(
        summary_frame, columns=summary_cols, show="headings", height=5
    )
    vsb_summary = ttk.Scrollbar(
        summary_frame, orient="vertical", command=summary_tree.yview
    )
    summary_tree.configure(yscrollcommand=vsb_summary.set)
    vsb_summary.pack(side="right", fill="y")
    summary_tree.pack(side="left", fill="both", expand=True)

    numeric_cols = {
        # internal keys
        "kolicina_norm",
        "vrednost",
        "rabata_pct",
        "neto_po_rabatu",
        # display heads (if summary_cols contains headers)
        "Količina",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    }
    for c, h in zip(summary_cols, summary_heads):
        summary_tree.heading(c, text=h)
        summary_tree.column(
            c,
            width=120 if c in numeric_cols else 200,
            anchor="e" if c in numeric_cols else "w",
        )

    def _render_summary(df_summary: pd.DataFrame) -> None:
        for item in summary_tree.get_children():
            summary_tree.delete(item)
        for _, row in df_summary.iterrows():
            vals = [
                row["WSM šifra"],
                row["WSM Naziv"],
                _fmt(_clean_neg_zero(row["Količina"])),
                _fmt(_clean_neg_zero(row["Znesek"])),
                _fmt(_clean_neg_zero(row.get("Rabat (%)", Decimal("0.00")))),
                _fmt(_clean_neg_zero(row["Neto po rabatu"])),
            ]
            summary_tree.insert("", "end", values=vals)
            log.info(
                "SUMMARY[%s] cena=%s",
                row["WSM šifra"],
                row.get("Neto po rabatu"),
            )
        log.debug(f"Povzetek posodobljen: {len(df_summary)} WSM šifer")

    def _update_summary():
        import pandas as pd
        from decimal import Decimal
        from wsm.ui.review.helpers import (
            ensure_eff_discount_col,
            first_existing_series,
        )

        df = globals().get("_CURRENT_GRID_DF")
        if df is None:
            df = globals().get("df")
        if df is None or df.empty:
            _render_summary(summary_df_from_records([]))
            return

        # pred povzetkom vedno znova izpelji _summary_key iz _booked_sifra
        if "_booked_sifra" in df.columns:
            df["_summary_key"] = (
                df["_booked_sifra"]
                .astype(object)
                .where(~pd.isna(df["_booked_sifra"]), "OSTALO")
                .replace(
                    {
                        None: "OSTALO",
                        "": "OSTALO",
                        "<NA>": "OSTALO",
                        "nan": "OSTALO",
                        "NaN": "OSTALO",
                    }
                )
            )
        else:
            # fallback – če česa manjka, vse pod OSTALO
            df["_summary_key"] = "OSTALO"
            try:
                df["_summary_key"] = df["_summary_key"].reindex(df.index)
            except Exception:
                pass

        # že zagotovljen v review_links; če ni, ga dodamo
        ensure_eff_discount_col(df)

        # Vzemi potrebne stolpce čim bolj robustno
        val_s = first_existing_series(
            df, ["Neto po rabatu", "Skupna neto", "vrednost", "total_net"]
        )
        bruto_s = first_existing_series(
            df, ["Bruto", "vrednost_bruto", "Skupna bruto", "vrednost"]
        )
        qty_s = first_existing_series(df, ["Količina", "kolicina_norm"])

        # Najprej dejanski grid-stolpec (posodablja se ob potrditvi),
        # nato morebitni display stolpec, nazadnje fallback.
        wsm_s = first_existing_series(
            df, ["wsm_sifra", "WSM šifra", "_summary_key"]
        )

        # Naziv prav tako iz grida, če obstaja
        name_s = first_existing_series(
            df, ["WSM Naziv", "WSM naziv", "wsm_naziv"]
        )
        if name_s is None:
            name_s = pd.Series([""] * len(df), index=df.index, dtype=object)

        # normaliziraj ključ na "OSTALO" kjer je prazno/NA
        if wsm_s is not None:
            wsm_s = (
                wsm_s.astype(object)
                .where(~pd.isna(wsm_s), "OSTALO")
                .replace(
                    {
                        None: "OSTALO",
                        "": "OSTALO",
                        "<NA>": "OSTALO",
                        "nan": "OSTALO",
                        "NaN": "OSTALO",
                    }
                )
            )

        # če je ključ OSTALO => naziv naj bo vedno "ostalo"
        name_s = name_s.astype(object).fillna("")
        if wsm_s is not None:
            name_s = name_s.where(~wsm_s.astype(str).eq("OSTALO"), "ostalo")

        # Če ključni stolpci manjkajo, izpiši prazen povzetek
        if wsm_s is None or val_s is None:
            _render_summary(summary_df_from_records([]))
            return

        work = pd.DataFrame(
            {
                "wsm_sifra": (
                    wsm_s
                    if wsm_s is not None
                    else pd.Series(["OSTALO"] * len(df), index=df.index)
                ),
                "wsm_naziv": name_s,
                "znesek": (
                    val_s
                    if val_s is not None
                    else pd.Series([Decimal("0")] * len(df), index=df.index)
                ),
                "kolicina": (
                    qty_s
                    if qty_s is not None
                    else pd.Series([Decimal("0")] * len(df), index=df.index)
                ),
                "bruto": (
                    bruto_s
                    if bruto_s is not None
                    else pd.Series([Decimal("0")] * len(df), index=df.index)
                ),
                "eff_discount_pct": df["eff_discount_pct"],
            }
        )
        # Izračunaj knjiženost enkrat in jo nesi naprej skozi agregacijo
        try:
            work["_is_booked"] = _booked_mask_from(work)
        except Exception:
            _ws = work["wsm_sifra"].fillna("").astype(str).str.strip()
            work["_is_booked"] = _ws.ne("") & ~_ws.str.upper().isin(
                globals().get("EXCLUDED_CODES", set())
            )
        log.info(
            "SUMMARY pre-group booked=%d / %d",
            int(work["_is_booked"].sum()),
            len(work),
        )

        # Po želji pokaži le knjižene postavke (uporabi isto masko)
        if globals().get("ONLY_BOOKED_IN_SUMMARY"):
            work = work[work["_is_booked"]]
            if work.empty:
                _render_summary(summary_df_from_records([]))
                return
        group_by_discount = globals().get("GROUP_BY_DISCOUNT", True)
        if group_by_discount and "_discount_bucket" in df.columns:
            work["_discount_bucket"] = df["_discount_bucket"]

        # Decimal-varno seštevanje
        def dsum(s):
            tot = Decimal("0")
            for v in s:
                try:
                    tot += v if isinstance(v, Decimal) else Decimal(str(v))
                except Exception:
                    pass
            return tot

        if {"wsm_sifra", "wsm_naziv"}.issubset(
            work.columns
        ) and "naziv" in df.columns:
            # neknjižene vrstice (brez prave WSM šifre)
            mask_unbooked = ~work["_is_booked"]
            # poravnaj dobaviteljev naziv na indekse 'work'
            src_names = df["naziv"].astype(str).reindex(work.index)
            # pri neknjiženih uporabi dobaviteljev naziv, da ne bo vse 'OSTALO'
            work.loc[mask_unbooked, "wsm_naziv"] = src_names[mask_unbooked]

        group_cols = ["wsm_sifra", "wsm_naziv", "eff_discount_pct"]
        if group_by_discount and "_discount_bucket" in work.columns:
            group_cols.append("_discount_bucket")
        g = work.groupby(group_cols, dropna=False, as_index=False).agg(
            {
                "znesek": dsum,
                "kolicina": dsum,
                "bruto": dsum,
                "_is_booked": "max",
            }
        )
        log.info(
            "SUMMARY post-group booked_groups=%d / %d",
            int((g["_is_booked"] > 0).sum()),
            len(g),
        )

        records = []
        for _, r in g.iterrows():
            code = str(r["wsm_sifra"] or "").strip()
            name = str(r["wsm_naziv"] or "").strip()
            is_booked = bool(r["_is_booked"])

            records.append(
                {
                    # Povzetek: knjižene po šifri+nazivu, ostalo v eno vrstico
                    "WSM šifra": (code if is_booked else "OSTALO"),
                    "WSM Naziv": (
                        name
                        if is_booked and name
                        else (code if is_booked else "ostalo")
                    ),
                    "Količina": r["kolicina"],
                    "Znesek": (
                        r["bruto"] if bruto_s is not None else r["znesek"]
                    ),
                    "Rabat (%)": (
                        r["eff_discount_pct"].quantize(Decimal("0.01"))
                        if isinstance(r["eff_discount_pct"], Decimal)
                        else Decimal(str(r["eff_discount_pct"])).quantize(
                            Decimal("0.01")
                        )
                    ),
                    "Neto po rabatu": r["znesek"],
                }
            )

        df_summary = summary_df_from_records(records)

        df_summary["WSM šifra"] = (
            first_existing_series(df_summary, ["WSM šifra", "wsm_sifra"])
            .fillna("")
            .astype(str)
        )
        df_summary["WSM Naziv"] = (
            first_existing_series(
                df_summary, ["WSM Naziv", "WSM naziv", "wsm_naziv"]
            )
            .fillna("")
            .astype(str)
        )
        try:
            bm = _booked_mask_from(df_summary)
        except Exception:
            _ws = df_summary["WSM šifra"].fillna("").astype(str).str.strip()
            bm = _ws.ne("") & ~_ws.str.upper().isin(
                globals().get("EXCLUDED_CODES", set())
            )
        log.info(
            "SUMMARY booked=%d, unbooked=%d", int(bm.sum()), int((~bm).sum())
        )

        # Konsolidiraj knjižene po šifri in izberi prvi neprazen naziv
        if {"WSM šifra", "WSM Naziv"}.issubset(df_summary.columns):
            booked_mask = bm
            if booked_mask.any():
                num_cols = [
                    c
                    for c in ["Količina", "Znesek", "Neto po rabatu"]
                    if c in df_summary.columns
                ]
                group_keys = ["WSM šifra"]
                if "Rabat (%)" in df_summary.columns:
                    group_keys.append("Rabat (%)")
                names = (
                    df_summary.loc[booked_mask, ["WSM šifra", "WSM Naziv"]]
                    .replace({"WSM Naziv": {"": None}})
                    .dropna(subset=["WSM Naziv"])
                    .drop_duplicates("WSM šifra")
                )
                sums = (
                    df_summary.loc[booked_mask]
                    .groupby(group_keys, dropna=False, as_index=False)[
                        num_cols
                    ]
                    .sum()
                )
                df_b = sums.merge(names, on="WSM šifra", how="left")
                df_b["WSM Naziv"] = df_b["WSM Naziv"].fillna(df_b["WSM šifra"])
                df_summary = pd.concat(
                    [df_b, df_summary.loc[~booked_mask]], ignore_index=True
                )

        # --- Vse neknjižene normaliziraj v ENO vrstico "ostalo" ---
        if ("WSM šifra" in df_summary.columns) and (
            "WSM Naziv" in df_summary.columns
        ):
            sifra = df_summary["WSM šifra"].fillna("").astype(str).str.strip()
            naziv = df_summary["WSM Naziv"].fillna("").astype(str)
            mask_ostalo = (
                sifra.eq("")
                | sifra.str.upper().isin(
                    globals().get("EXCLUDED_CODES", set())
                )
                | naziv.str.lower().eq("ostalo")
            )

            if mask_ostalo.any():
                # nastavi oznake za ostalo
                df_summary.loc[mask_ostalo, "WSM šifra"] = ""
                df_summary.loc[mask_ostalo, "WSM Naziv"] = "ostalo"

                # poskrbi, da "Rabat (%)" vedno obstaja
                if "Rabat (%)" not in df_summary.columns:
                    df_summary["Rabat (%)"] = Decimal("0.00")
                # za "ostalo" naj bo 0.00 in naj se ne deli po rabatu
                df_summary.loc[mask_ostalo, "Rabat (%)"] = Decimal("0.00")

                num_cols = [
                    c
                    for c in ["Količina", "Znesek", "Neto po rabatu"]
                    if c in df_summary.columns
                ]
                key_cols = [
                    c
                    for c in ["WSM šifra", "WSM Naziv"]
                    if c in df_summary.columns
                ]

                if num_cols and key_cols:
                    # agregiraj SAMO "ostalo"
                    agg = (
                        df_summary.loc[mask_ostalo, key_cols + num_cols]
                        .groupby(key_cols, dropna=False, as_index=False)
                        .sum()
                    )
                    # po agregaciji vrni "Rabat (%)" = 0.00
                    agg["Rabat (%)"] = Decimal("0.00")

                    # poravnaj stolpce in zamenjaj "ostalo" del
                    cols = list(df_summary.columns)
                    agg = agg.reindex(columns=cols, fill_value=None)
                    df_summary = pd.concat(
                        [df_summary.loc[~mask_ostalo, cols], agg],
                        ignore_index=True,
                    )

        # "ostalo" potisni na konec
        if "WSM Naziv" in df_summary.columns:
            order = (
                df_summary["WSM Naziv"].astype(str).str.lower() == "ostalo"
            ).astype(int)
            df_summary = (
                df_summary.assign(_o=order)
                .sort_values(["_o", "WSM Naziv"])
                .drop(columns="_o")
            )

        _render_summary(df_summary)

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

    lbl_net = tk.Label(
        total_frame,
        text=f"Neto: {net:,.2f} €",
        font=("Arial", 10, "bold"),
        name="total_net",
    )
    lbl_net.pack(side="left", padx=10)
    lbl_vat = tk.Label(
        total_frame,
        text=f"DDV: {vat:,.2f} €",
        font=("Arial", 10, "bold"),
        name="total_vat",
    )
    lbl_vat.pack(side="left", padx=10)
    lbl_gross = tk.Label(
        total_frame,
        text=f"Skupaj: {gross:,.2f} €",
        font=("Arial", 10, "bold"),
        name="total_gross",
    )
    lbl_gross.pack(side="left", padx=10)

    # Placeholder label for backward compatibility with tests expecting a
    # single ``total_sum`` widget.
    tk.Label(total_frame, name="total_sum")

    style = ttk.Style()
    style.configure("Indicator.Green.TLabel", foreground="green")
    style.configure("Indicator.Red.TLabel", foreground="red")
    indicator_label = ttk.Label(
        total_frame, text="", style="Indicator.Red.TLabel"
    )
    indicator_label.pack(side="left", padx=5)
    status_count_label = ttk.Label(total_frame, text="")
    status_count_label.pack(side="left", padx=5)

    _last_warn_msg = {"val": None}

    def _safe_update_totals():
        if closing or not root.winfo_exists():
            return

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
        difference = abs(diff)
        try:
            discount = doc_discount
        except NameError:  # backward compatibility
            discount = doc_discount_total
        if difference > tolerance:
            if discount:
                diff2 = inv_total - (calc_total + abs(discount))
                if abs(diff2) > tolerance:
                    msg = (
                        "Razlika med postavkami in računom je "
                        f"{diff2:+.2f} € in presega dovoljeno zaokroževanje."
                    )
                    if _last_warn_msg["val"] != msg:
                        _last_warn_msg["val"] = msg
                        messagebox.showwarning("Opozorilo", msg)
            else:
                msg = (
                    "Razlika med postavkami in računom je "
                    f"{diff:+.2f} € in presega dovoljeno zaokroževanje."
                )
                if _last_warn_msg["val"] != msg:
                    _last_warn_msg["val"] = msg
                    messagebox.showwarning("Opozorilo", msg)
        else:
            # razlika je OK -> dovoli prihodnja opozorila
            _last_warn_msg["val"] = None

        net = net_total
        vat = vat_val
        gross = calc_total
        try:
            if indicator_label is None or not indicator_label.winfo_exists():
                return
            booked_mask = (
                _booked_mask_from(df["wsm_sifra"])
                if "wsm_sifra" in df.columns
                else None
            )
            if booked_mask is not None and "wsm_naziv" in df.columns:
                nm = (
                    df["wsm_naziv"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )
                booked_mask = booked_mask & (nm != "OSTALO")
            booked = int(booked_mask.sum()) if booked_mask is not None else 0
            remaining = (
                (len(df) - booked) if booked_mask is not None else len(df)
            )
            indicator_label.config(
                text="✓" if difference <= tolerance else "✗",
                style=(
                    "Indicator.Green.TLabel"
                    if difference <= tolerance
                    else "Indicator.Red.TLabel"
                ),
            )
            try:
                if status_count_label and status_count_label.winfo_exists():
                    status_count_label.config(
                        text=f"Knjiženo: {booked}  Ostane: {remaining}"
                    )
            except Exception:
                pass
        except tk.TclError:
            return
        widget = total_frame.children.get("total_net")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"Neto: {net:,.2f} €")
        widget = total_frame.children.get("total_vat")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"DDV: {vat:,.2f} €")
        widget = total_frame.children.get("total_gross")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"Skupaj: {gross:,.2f} €")
        widget = total_frame.children.get("total_sum")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(
                text=(
                    f"Neto:   {net:,.2f} €\n"
                    f"DDV:    {vat:,.2f} €\n"
                    f"Skupaj: {gross:,.2f} €"
                )
            )

    def _schedule_totals():
        nonlocal _after_totals_id
        if closing or not root.winfo_exists():
            return
        _after_totals_id = root.after(250, _safe_update_totals)

    def _on_close(_=None):
        nonlocal closing, _after_totals_id
        closing = True
        try:
            if _after_totals_id:
                root.after_cancel(_after_totals_id)
        except Exception:
            pass
        _cleanup()
        try:
            root.destroy()
        except tk.TclError:
            pass

    bottom = None  # backward-compatible placeholder for tests  # noqa: F841
    entry_frame = tk.Frame(root)
    entry_frame.pack(fill="x", padx=8)

    entry = ttk.Entry(entry_frame, width=120)
    lb = tk.Listbox(entry_frame, height=6)
    parent = entry.master
    try:
        parent.columnconfigure(0, weight=1)
    except Exception:
        pass
    entry.grid_configure(row=0, column=0, pady=5, sticky="ew")
    lb.grid_configure(row=1, column=0, sticky="ew")
    lb.grid_remove()

    btn_frame = tk.Frame(entry_frame)
    btn_frame.grid(row=2, column=0, pady=(0, 6), sticky="ew")

    # --- Unit change widgets ---
    unit_options = ["kos", "kg", "L"]

    def _cleanup():
        nonlocal closing, price_tip, last_warn_item
        closing = True
        if header_after_id:
            try:
                root.after_cancel(header_after_id)
            except Exception:
                pass
        for widget, seq in bindings:
            try:
                widget.unbind(seq)
            except Exception:
                pass
        if price_tip is not None:
            try:
                price_tip.destroy()
            except Exception:
                pass
            price_tip = None
            last_warn_item = None

    def _finalize_and_save(_=None):
        _update_summary()
        _safe_update_totals()
        _cleanup()
        if is_toplevel:
            original_quit = root.quit
            root.quit = root.destroy
        else:
            original_quit = None
        try:
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
        finally:
            if original_quit is not None:
                root.quit = original_quit

    save_btn = tk.Button(
        btn_frame,
        text="Shrani & zapri",
        width=14,
        command=_finalize_and_save,
    )

    def _exit():
        _on_close()

    exit_btn = tk.Button(
        btn_frame,
        text="Izhod",
        width=14,
        command=_exit,
    )
    save_btn.grid(row=0, column=0, padx=(6, 0))
    exit_btn.grid(row=0, column=1, padx=(6, 0))

    root.bind("<F10>", _finalize_and_save)
    bindings.append((root, "<F10>"))

    nazivi = wsm_df["wsm_naziv"].dropna().tolist()
    n2s = dict(zip(wsm_df["wsm_naziv"], wsm_df["wsm_sifra"]))

    _accepting_enter = False
    _suggest_on_focus = {"val": False}

    def _dropdown_is_open(widget: tk.Listbox) -> bool:
        return widget.winfo_ismapped()

    def _close_suggestions(
        entry_widget: ttk.Entry, lb_widget: tk.Listbox
    ) -> None:
        """Hide the suggestion listbox and reset selection."""
        if not _dropdown_is_open(lb_widget):
            return
        lb_widget.grid_remove()
        lb_widget.selection_clear(0, "end")
        entry_widget.focus_set()

    def _accept_current_suggestion(
        entry_widget: ttk.Entry, lb_widget: tk.Listbox
    ):
        """Insert the selected suggestion into the entry widget."""
        if lb_widget.curselection():
            value = lb_widget.get(lb_widget.curselection()[0])
            entry_widget.delete(0, "end")
            entry_widget.insert(0, value)
            entry_widget.icursor("end")
        lb_widget.selection_clear(0, "end")
        _close_suggestions(entry_widget, lb_widget)
        return "break"

    def _start_edit(_=None):
        if not tree.focus():
            return "break"
        entry.delete(0, "end")
        _close_suggestions(entry, lb)
        _suggest_on_focus["val"] = True
        entry.focus_set()
        try:
            _open_suggestions_if_needed()
        except Exception:
            pass
        return "break"

    def _open_suggestions_if_needed():
        """Open the suggestion dropdown if it's not already visible."""
        txt = entry.get().strip().lower()
        lb.delete(0, "end")
        matches = [n for n in nazivi if not txt or txt in n.lower()]
        if matches:
            lb.grid()
            lb.update_idletasks()
            lb.lift()
            for m in matches:
                lb.insert("end", m)
            lb.selection_set(0)
            lb.activate(0)
            lb.see(0)
        else:
            _close_suggestions(entry, lb)

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
        if _dropdown_is_open(lb) and not lb.curselection():
            _close_suggestions(entry, lb)
        txt = entry.get().strip().lower()
        lb.delete(0, "end")
        if not txt:
            _close_suggestions(entry, lb)
            return
        matches = [n for n in nazivi if txt in n.lower()]
        if matches:
            lb.grid()
            lb.update_idletasks()
            lb.lift()
            for m in matches:
                lb.insert("end", m)
            lb.selection_set(0)
            lb.activate(0)
            lb.see(0)
        else:
            _close_suggestions(entry, lb)

    def _init_listbox(evt=None):
        """Give focus to the listbox and handle initial navigation."""
        if _dropdown_is_open(lb):
            lb.focus_set()
            if not lb.curselection():
                lb.selection_set(0)
                lb.activate(0)
                lb.see(0)
            if evt and evt.keysym == "Down":
                _nav_list(evt)
        return "break"

    def _confirm_and_move_down() -> None:
        nonlocal _accepting_enter
        if _accepting_enter:
            return
        _accepting_enter = True
        try:
            _confirm()
            _suggest_on_focus["val"] = False
            try:
                globals()["_CURRENT_GRID_DF"] = df
                _update_summary()
                _schedule_totals()
            except Exception:
                pass
            # → premik na NASLEDNJO vrstico
            cur = tree.focus()
            next_iid = tree.next(cur) or cur
            tree.selection_set(next_iid)
            tree.focus(next_iid)
            tree.see(next_iid)
            if EDIT_ON_ENTER:
                tree.focus_set()  # vnos se NE odpre – čaka na Enter
            else:
                entry.focus_set()
        finally:
            _accepting_enter = False

    def _on_focus_in(e):
        if _suggest_on_focus["val"]:
            _open_suggestions_if_needed()

    def _start_editing_from_tree(_evt=None):
        """Enter na tabeli začne vnos (focus v Entry + predlogi)."""
        try:
            entry.focus_set()
            _open_suggestions_if_needed()
        except Exception:
            pass
        return "break"

    def _on_return_accept(evt=None):
        nonlocal _accepting_enter
        if _accepting_enter:
            return "break"
        if _dropdown_is_open(lb):
            _accept_current_suggestion(entry, lb)
            entry.after(0, _confirm_and_move_down)
            return "break"
        # tudi brez dropdowna potrdi in pojdi na naslednjo vrstico
        entry.after(0, _confirm_and_move_down)
        return "break"

    def _on_entry_focus_out(evt):
        if entry.focus_get() is lb:
            return
        entry.after(10, lambda: _close_suggestions(entry, lb))

    def _on_entry_escape(evt):
        _close_suggestions(entry, lb)
        return "break"

    def _lb_escape(_):
        _close_suggestions(entry, lb)
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
            _schedule_totals()
            top.destroy()

        tk.Button(top, text="OK", command=_apply).pack(pady=(0, 10))
        cb.bind("<Return>", _apply)
        cb.focus_set()
        return "break"

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
        # vizualni tagi
        try:
            tags = set(tree.item(sel_i, "tags"))
            tags.discard("unbooked")
            tags.add("linked")
            tree.item(sel_i, tags=tuple(tags))
        except Exception:
            pass
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
        # ohrani obstoječe tage; samo dodaj/odstrani "price_warn"
        try:
            tset = set(tree.item(sel_i, "tags"))
            if warn:
                tset.add("price_warn")
            else:
                tset.discard("price_warn")
            tree.item(sel_i, tags=tuple(tset))
        except Exception:
            pass

        df.at[idx, "warning"] = tooltip

        _show_tooltip(sel_i, tooltip)
        if "is_gratis" in df.columns and df.at[idx, "is_gratis"]:
            tset = set(tree.item(sel_i).get("tags", ()))
            tset.add("gratis")
            tree.item(sel_i, tags=tuple(tset))
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
        # Naj bodo “display” stolpci vedno poravnani z internimi
        # (pred summary!)
        try:
            df.at[idx, "WSM šifra"] = df.at[idx, "wsm_sifra"]
        except Exception:
            pass
        try:
            if "wsm_naziv" in df.columns:
                df.at[idx, "WSM Naziv"] = df.at[idx, "wsm_naziv"]
        except Exception:
            pass
        try:
            globals()["_CURRENT_GRID_DF"] = df
            _update_summary()
        except Exception:
            pass
        log.info(
            "Potrjeno: idx=%s, wsm_sifra=%s, sifra_dobavitelja=%s",
            idx,
            df.at[idx, "wsm_sifra"],
            df.at[idx, "sifra_dobavitelja"],
        )
        entry.delete(0, "end")
        _close_suggestions(entry, lb)
        lb.selection_clear(0, "end")
        tree.focus_set()
        return "break"

    def _apply_multiplier_prompt(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        multiplier_val = simpledialog.askinteger(
            "Množitelj", "Vnesi množitelj:", parent=root, minvalue=2
        )
        if not multiplier_val:
            return "break"
        _apply_multiplier(
            df,
            int(sel_i),
            Decimal(multiplier_val),
            tree,
            _update_summary,
            _schedule_totals,
        )
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
        # vizualni tagi
        try:
            tags = set(tree.item(sel_i, "tags"))
            tags.discard("linked")
            tags.discard("price_warn")  # remove warning tag when unbooking
            tags.add("unbooked")
            tree.item(sel_i, tags=tuple(tags))
        except Exception:
            pass
        # počisti opozorilo/tooltip
        if "warning" in df.columns:
            df.at[idx, "warning"] = ""
        _hide_tooltip()
        log.debug(
            f"Povezava odstranjena: idx={idx}, wsm_naziv=NaN, wsm_sifra=NaN"
        )
        try:
            globals()["_CURRENT_GRID_DF"] = df
            _update_summary()
        except Exception:
            pass
        _schedule_totals()  # Update totals after clearing
        tree.focus_set()
        return "break"

    multiplier_btn = tk.Button(
        btn_frame,
        text="Pomnoži z količino X",
        command=_apply_multiplier_prompt,
    )
    multiplier_btn.grid(row=0, column=2, padx=(6, 0))

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
    if EDIT_ON_ENTER:
        tree.bind("<Return>", _start_editing_from_tree)
        tree.bind("<KP_Enter>", _start_editing_from_tree)
        tree.bind("<F2>", _start_editing_from_tree)
    else:
        tree.bind("<Return>", _start_edit)
        tree.bind("<KP_Enter>", _start_edit)
        tree.bind("<F2>", _start_edit)
    bindings.append((tree, "<Return>"))
    bindings.append((tree, "<KP_Enter>"))
    bindings.append((tree, "<F2>"))
    tree.bind("<BackSpace>", _clear_wsm_connection)
    bindings.append((tree, "<BackSpace>"))
    tree.bind("<Control-m>", _apply_multiplier_prompt)
    bindings.append((tree, "<Control-m>"))
    tree.bind("<Up>", _tree_nav_up)
    bindings.append((tree, "<Up>"))
    tree.bind("<Down>", _tree_nav_down)
    bindings.append((tree, "<Down>"))
    tree.bind("<Double-Button-1>", _edit_unit)
    bindings.append((tree, "<Double-Button-1>"))
    tree.bind("<<TreeviewSelect>>", _on_select)
    bindings.append((tree, "<<TreeviewSelect>>"))

    # Vezave za entry in lb
    entry.bind("<FocusIn>", _on_focus_in)
    bindings.append((entry, "<FocusIn>"))
    entry.bind("<KeyRelease>", _suggest)
    bindings.append((entry, "<KeyRelease>"))
    entry.bind("<Down>", _init_listbox)
    bindings.append((entry, "<Down>"))
    entry.bind("<Tab>", _init_listbox)
    bindings.append((entry, "<Tab>"))
    entry.bind("<Right>", _init_listbox)
    bindings.append((entry, "<Right>"))
    entry.bind("<Return>", _on_return_accept)
    bindings.append((entry, "<Return>"))
    entry.bind("<KP_Enter>", _on_return_accept)
    bindings.append((entry, "<KP_Enter>"))
    entry.bind("<FocusOut>", _on_entry_focus_out)
    bindings.append((entry, "<FocusOut>"))
    entry.bind("<Escape>", _on_entry_escape)
    bindings.append((entry, "<Escape>"))
    lb.bind("<Return>", _on_return_accept)
    bindings.append((lb, "<Return>"))
    lb.bind("<KP_Enter>", _on_return_accept)
    bindings.append((lb, "<KP_Enter>"))
    lb.bind("<Escape>", _lb_escape)
    bindings.append((lb, "<Escape>"))

    def _on_lb_click(_):
        # izberi element pod miško, še preden potrdiš
        try:
            i = lb.nearest(lb.winfo_pointery() - lb.winfo_rooty())
            lb.selection_clear(0, "end")
            lb.selection_set(i)
        except Exception:
            pass
        _accept_current_suggestion(entry, lb)
        lb.after(0, _confirm_and_move_down)

    lb.bind("<ButtonRelease-1>", _on_lb_click)
    bindings.append((lb, "<ButtonRelease-1>"))
    lb.bind("<Double-Button-1>", _on_lb_click)
    bindings.append((lb, "<Double-Button-1>"))
    lb.bind("<Down>", _nav_list)
    bindings.append((lb, "<Down>"))
    lb.bind("<Up>", _nav_list)
    bindings.append((lb, "<Up>"))

    # Prvič osveži
    _update_summary()
    _schedule_totals()

    if is_toplevel:
        root.protocol("WM_DELETE_WINDOW", _on_close)
        root.wait_window()
    else:
        root.protocol("WM_DELETE_WINDOW", _on_close)
        root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    if GROUP_BY_DISCOUNT and "_discount_bucket" in df.columns:
        df = df.drop(columns=["_discount_bucket"])

    return pd.concat([df, df_doc], ignore_index=True)
