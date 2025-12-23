# File: wsm/ui/review/gui.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Tuple, Optional, Any
from types import SimpleNamespace

import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import builtins
from lxml import etree as LET
from os import environ, getenv

from wsm.utils import short_supplier_name, _clean, _build_header_totals
from wsm.constants import (
    PRICE_DIFF_THRESHOLD,
    DEFAULT_TOLERANCE,
    SMART_TOLERANCE_ENABLED,
    TOLERANCE_BASE,
    MAX_TOLERANCE,
    ROUNDING_CORRECTION_ENABLED,
)
from wsm.parsing.eslog import (
    XML_PARSER,
    extract_invoice_number,
    extract_service_date,
    get_supplier_info_vat,
)
from wsm.supplier_store import _norm_vat
from .helpers import (
    _fmt,
    _norm_unit,
    _merge_same_items,
    _apply_price_warning,
    first_existing_series,  # ← potrebujemo v _backfill_* in drugje
    _first_scalar,
    _norm_wsm_code,
)
from .io import _save_and_close, _load_supplier_map
from .summary_columns import SUMMARY_COLS, SUMMARY_KEYS, SUMMARY_HEADS
from .summary_utils import summary_df_from_records

builtins.tk = tk
builtins.simpledialog = simpledialog

# Feature flag controlling whether editing starts only after pressing Enter
EDIT_ON_ENTER = getenv("WSM_EDIT_ON_ENTER", "1") not in {
    "0",
    "false",
    "False",
}

# Feature flag controlling whether items are grouped by discount/price
GROUP_BY_DISCOUNT = getenv("WSM_GROUP_BY_DISCOUNT", "1") not in {
    "0",
    "false",
    "False",
}

# Should the summary include only booked items? (default NO)
ONLY_BOOKED_IN_SUMMARY = getenv("WSM_SUMMARY_ONLY_BOOKED", "0") not in {
    "0",
    "false",
    "False",
}

# Naj se shranjene povezave uporabijo samodejno ob odprtju?
# (privzeto NE)
_AUTO_APPLY_ENV = getenv("WSM_AUTO_APPLY_LINKS")
_AUTO_APPLY_ENV_SRC = "WSM_AUTO_APPLY_LINKS"
if _AUTO_APPLY_ENV is None:
    _AUTO_APPLY_ENV = getenv("AUTO_APPLY_LINKS", "0")
    _AUTO_APPLY_ENV_SRC = "AUTO_APPLY_LINKS"
AUTO_APPLY_LINKS_RAW = _AUTO_APPLY_ENV
AUTO_APPLY_LINKS_SOURCE = _AUTO_APPLY_ENV_SRC
AUTO_APPLY_LINKS = _AUTO_APPLY_ENV not in {
    "0",
    "false",
    "False",
}

# Ali naj pri knjiženih vrsticah prepišemo tudi 'Ostalo' z nazivom iz kataloga?
OVERWRITE_OSTALO_IN_GRID = getenv(
    "WSM_OVERWRITE_OSTALO_IN_GRID", "1"
) not in {"0", "false", "False"}

# Ali naj se med urejanjem prikazujejo predlogi WSM nazivov? (privzeto DA)
ENABLE_WSM_SUGGESTIONS = getenv(
    "WSM_ENABLE_SUGGESTIONS", "1"
) not in {"0", "false", "False", ""}

DEC2 = Decimal("0.01")
DEC_PCT_MIN = Decimal("-100")
DEC_PCT_MAX = Decimal("100")
DEC_SMALL_DISCOUNT = Decimal("0.1")

EXCLUDED_CODES = {"UNKNOWN", "OSTALO", "OTHER", "NAN"}


def _excluded_codes_upper() -> frozenset[str]:
    """Return ``EXCLUDED_CODES`` uppercased.

    Evaluated on each call so tests/plugins may adjust ``EXCLUDED_CODES`` at
    runtime without stale cached values.
    """
    return frozenset(x.upper() for x in EXCLUDED_CODES)


# Normalizira knjiženo kodo: prazna/None ali izključena šifra -> "OSTALO".
def _coerce_booked_code(code: object) -> str:
    norm = _norm_wsm_code(code)
    if not norm:
        return "OSTALO"
    upper = norm.upper()
    if upper in _excluded_codes_upper():
        return "OSTALO"
    return norm


# Regex za prepoznavo "glavin" vrstic (Dobavnica/Račun/...).
# Možno razširiti z okoljsko spremenljivko ``WSM_HEADER_PREFIX``.
HDR_PREFIX_RE = re.compile(
    environ.get(
        "WSM_HEADER_PREFIX",
        (
            r"(?i)^\s*(Dobavnica|Ra[cč]un|Predra[cč]un|"
            r"Dobropis|Bremepis|Storno|Stornirano)\b"
        ),
    )
)


def _mask_header_like_rows(
    df: pd.DataFrame,
    name_col: str = "naziv",
    qty_col: str = "kolicina_norm",
    val_col: str = "vrednost",
    eps: float = 1e-9,
) -> pd.Series:
    """Return True for rows that look like document headers.

    Header-like rows ("Dobavnica", "Račun", etc.) often appear in the XML
    with zero quantity and zero value.  Such rows should be hidden from the
    review grid and summary.
    """

    name = (
        df.get(name_col, pd.Series([""] * len(df), index=df.index))
        .fillna("")
        .astype(str)
    )
    qty = pd.to_numeric(
        df.get(qty_col, pd.Series([0] * len(df), index=df.index)),
        errors="coerce",
    ).fillna(0)
    val = pd.to_numeric(
        df.get(val_col, pd.Series([0] * len(df), index=df.index)),
        errors="coerce",
    ).fillna(0)
    return (
        name.str.match(HDR_PREFIX_RE, na=False)
        & (qty.abs() <= eps)
        & (val.abs() <= eps)
    )


def _normalize_wsm_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enotno poimenuj prikazne stolpce v gridu:
      - 'WSM naziv' (mali n) -> 'WSM Naziv' (veliki N)
      - posodobi 'WSM šifra' in 'WSM Naziv' iz baznih
        'wsm_sifra' / 'wsm_naziv'.
    Ne povzroči napake, če stolpcev ni.
    """
    if df is None or df.empty:
        return df
    # 1) preimenuj morebitni 'WSM naziv' -> 'WSM Naziv'
    rename_map = {}
    for c in list(df.columns):
        if c.strip().lower() == "wsm naziv".lower() and c != "WSM Naziv":
            rename_map[c] = "WSM Naziv"
    if rename_map:
        df = df.rename(columns=rename_map)
    # če imamo po naključju oba stolpca, združi in odstrani starega
    if "WSM naziv" in df.columns and "WSM Naziv" in df.columns:
        df["WSM Naziv"] = df["WSM Naziv"].fillna(df["WSM naziv"])
        df = df.drop(columns=["WSM naziv"])
    # 2) zapolni prikazne iz baznih, če obstajajo
    if "wsm_sifra" in df.columns:
        if "WSM šifra" not in df.columns:
            df["WSM šifra"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df["WSM šifra"] = df["wsm_sifra"].astype("string").fillna("")
    if "wsm_naziv" in df.columns:
        if "WSM Naziv" not in df.columns:
            df["WSM Naziv"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df["WSM Naziv"] = df["wsm_naziv"].astype("string").fillna("")
    return df


def _apply_links_to_df(
    df: pd.DataFrame,
    links_df: pd.DataFrame,
    *,
    apply_codes: bool = True,
) -> tuple[pd.DataFrame, int]:
    """Apply previously saved links to ``df``.

    Parameters
    ----------
    df:
        Current invoice lines that should receive saved metadata.
    links_df:
        DataFrame read from the Excel file with stored mappings.
    apply_codes:
        When ``True`` the WSM codes and booking status are restored.  When
        ``False`` only auxiliary information (``naziv_ckey`` normalization)
        is refreshed which is still required for multiplier lookups.

    Returns
    -------
    tuple[pandas.DataFrame, int]
        The (possibly modified) ``df`` and the number of rows where a WSM
        code was restored.
    """

    def _finalize(result_df: pd.DataFrame, count: int) -> tuple[pd.DataFrame, int]:
        log.info("Uveljavljeno %d povezav", count)
        return result_df, count

    if not isinstance(df, pd.DataFrame) or df.empty:
        return _finalize(df, 0)
    if not isinstance(links_df, pd.DataFrame) or links_df.empty:
        return _finalize(df, 0)

    log.info("=== MATCHING PROCESS ===")
    log.info("df ima %d vrstic", len(df))
    log.info("links_df ima %d vrstic", len(links_df))
    log.info("apply_codes=%s", apply_codes)
    log.debug(
        "Invoice keys:\n%s",
        df.reindex(columns=["sifra_dobavitelja", "naziv_ckey"]).head(),
    )
    log.debug(
        "Links keys:\n%s",
        links_df.reindex(columns=["sifra_dobavitelja", "naziv_ckey"]).head(),
    )

    if "sifra_dobavitelja" not in df.columns or "sifra_dobavitelja" not in links_df.columns:
        return _finalize(df, 0)

    def _strip_series(series: pd.Series) -> pd.Series:
        ser = series.astype("string").fillna("").replace("<NA>", "")
        return ser.str.strip()

    def _clean_series(series: pd.Series) -> pd.Series:
        ser = series.astype("string").fillna("").replace("<NA>", "")
        return ser.map(lambda val: _clean(val) if val else "")

    try:
        df["sifra_dobavitelja"] = _strip_series(df["sifra_dobavitelja"])
    except Exception:
        return _finalize(df, 0)

    if "naziv_ckey" in df.columns:
        df["naziv_ckey"] = _clean_series(df["naziv_ckey"])
    elif "naziv" in df.columns:
        df["naziv_ckey"] = _clean_series(df["naziv"])
    else:
        return _finalize(df, 0)

    link_df = links_df.copy()
    link_df["sifra_dobavitelja"] = _strip_series(link_df["sifra_dobavitelja"])
    if "naziv_ckey" in link_df.columns:
        link_df["naziv_ckey"] = _clean_series(link_df["naziv_ckey"])
    elif "naziv" in link_df.columns:
        link_df["naziv_ckey"] = _clean_series(link_df["naziv"])
    else:
        link_df["naziv_ckey"] = ""

    link_df = link_df.loc[
        (link_df["sifra_dobavitelja"] != "") & (link_df["naziv_ckey"] != "")
    ]
    log.info("Po filtriranju links_df ima %d vrstic", len(link_df))
    if not link_df.empty:
        log.debug(
            "Primer link_df ključev:\n%s",
            link_df[["sifra_dobavitelja", "naziv_ckey"]]
            .head(3)
            .to_string(),
        )
    if link_df.empty:
        return _finalize(df, 0)

    link_df = link_df.drop_duplicates(
        ["sifra_dobavitelja", "naziv_ckey"], keep="last"
    )
    link_idx = link_df.set_index(["sifra_dobavitelja", "naziv_ckey"])

    df_keys = list(zip(df["sifra_dobavitelja"], df["naziv_ckey"]))
    log.info("df_keys ima %d elementov", len(df_keys))
    log.debug("Prvi 3 df_keys: %s", df_keys[:3])
    try:
        matched = link_idx.reindex(df_keys)
    except Exception:
        matched = link_idx.loc[link_idx.index.intersection(df_keys)].reindex(df_keys)

    matched = matched.reset_index(drop=True)
    matched.index = df.index

    if "override_unit" in matched.columns:
        overrides = matched["override_unit"]
        try:
            overrides = overrides.astype("string")
        except Exception:
            overrides = overrides.astype(str)
        overrides = overrides.replace({"<NA>": ""}).fillna("").str.strip()
        cleaned = overrides.replace("", pd.NA)
        if "override_unit" not in df.columns:
            df["override_unit"] = pd.Series(pd.NA, index=df.index, dtype="string")
        df["override_unit"] = cleaned.astype("string")

    log.info("=== CODES APPLICATION ===")
    log.info("matched ima stolpce: %s", matched.columns.tolist())
    if "wsm_sifra" in matched.columns:
        valid_codes = matched["wsm_sifra"].notna().sum()
        log.info("matched ima %d veljavnih wsm_sifra", valid_codes)
        log.debug(
            "Primer wsm_sifra iz matched: %s",
            matched["wsm_sifra"].head().tolist(),
        )

    updated_count = 0
    if apply_codes and "wsm_sifra" in matched.columns:
        codes = matched["wsm_sifra"].map(_norm_wsm_code)
        if isinstance(codes, pd.Series):
            codes = codes.astype("string").fillna("").str.strip()
            mask = codes.ne("")
            updated_count = int(mask.sum()) if mask.any() else 0
            if updated_count:
                if "wsm_sifra" not in df.columns:
                    df["wsm_sifra"] = pd.Series(pd.NA, index=df.index, dtype="string")
                df.loc[mask, "wsm_sifra"] = codes.loc[mask]

                if "wsm_naziv" not in df.columns:
                    df["wsm_naziv"] = pd.Series(pd.NA, index=df.index, dtype="string")
                if "wsm_naziv" in matched.columns:
                    saved_names = (
                        matched["wsm_naziv"]
                        .astype("string")
                        .fillna("")
                        .str.strip()
                    )
                    df.loc[mask, "wsm_naziv"] = saved_names.loc[mask].where(
                        saved_names.loc[mask].ne(""), df.loc[mask, "wsm_naziv"]
                    )

                if "status" not in df.columns:
                    df["status"] = ""
                df.loc[mask, "status"] = "POVEZANO"

                if "dobavitelj" in matched.columns and "dobavitelj" in df.columns:
                    saved_suppliers = (
                        matched["dobavitelj"]
                        .astype("string")
                        .fillna("")
                        .str.strip()
                    )
                    df.loc[mask, "dobavitelj"] = saved_suppliers.loc[mask].where(
                        saved_suppliers.loc[mask].ne(""),
                        df.loc[mask, "dobavitelj"],
                    )

                if "_booked_sifra" in df.columns:
                    df.loc[mask, "_booked_sifra"] = codes.loc[mask]
                if "_summary_key" in df.columns:
                    df.loc[mask, "_summary_key"] = codes.loc[mask]
                if "WSM šifra" in df.columns:
                    df.loc[mask, "WSM šifra"] = codes.loc[mask]

                display_name_cols = [c for c in ("WSM Naziv", "WSM naziv") if c in df.columns]
                if display_name_cols:
                    display_names = (
                        df.loc[mask, "wsm_naziv"].astype("string").fillna("")
                    )
                    for col in display_name_cols:
                        df.loc[mask, col] = display_names

    return _finalize(df, updated_count)


def _fill_names_from_catalog(
    df: pd.DataFrame, wsm_df: pd.DataFrame
) -> pd.DataFrame:
    """Zapolni/poravna ``wsm_naziv`` iz kataloga glede na kodo."""
    if not isinstance(wsm_df, pd.DataFrame) or not {
        "wsm_sifra",
        "wsm_naziv",
    }.issubset(wsm_df.columns):
        return df
    if "wsm_sifra" not in df.columns:
        return df

    nm = (
        wsm_df.drop_duplicates("wsm_sifra")
        .assign(wsm_sifra=lambda d: d["wsm_sifra"].astype(str).str.strip())
        .set_index("wsm_sifra")["wsm_naziv"]
    )

    excluded = _excluded_codes_upper()
    codes = df["wsm_sifra"].astype(str).str.strip()
    has_code = codes.ne("") & ~codes.str.upper().isin(excluded)

    cur = df.get("wsm_naziv")
    if cur is None:
        df["wsm_naziv"] = pd.Series(pd.NA, index=df.index, dtype="string")
        cur = df["wsm_naziv"]
    cur_s = cur.astype(str)

    # manjkajoče ime ali 'ostalo' – slednje le, če stikalo to dovoli
    need_fill = cur.isna() | cur_s.str.strip().eq("")
    if globals().get("OVERWRITE_OSTALO_IN_GRID", True):
        need_fill = need_fill | cur_s.str.strip().str.lower().eq("ostalo")

    fill_mask = has_code & need_fill
    val = codes[fill_mask].map(nm)
    df.loc[fill_mask, "wsm_naziv"] = val.where(
        val.notna(), cur.loc[fill_mask]
    ).astype("string")
    return df


def _dec_or_zero(x):
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def _dec_or_none(x):
    try:
        return Decimal(str(x))
    except Exception:
        return None


def _zero_small_discount(val: object) -> Decimal:
    """Treat rounding-level discounts (|pct| < 0.1) as zero."""

    pct = _dec_or_zero(val)
    return Decimal("0") if pct.copy_abs() < DEC_SMALL_DISCOUNT else pct


def _ensure_eff_discount_pct(df: pd.DataFrame) -> pd.DataFrame:
    """
    UI uporablja eff_discount_pct za prikaz (kolona 'Rabat (%)' in za
    'Opozorilo'). Če je ta stolpec prazen ali 0, ga zapolnimo iz rabata_pct.
    """
    if "eff_discount_pct" not in df.columns:
        df["eff_discount_pct"] = None
    df["eff_discount_pct"] = df["eff_discount_pct"].apply(_dec_or_zero)
    if "rabata_pct" in df.columns:
        rp = df["rabata_pct"].apply(_dec_or_zero)
        mask = df["eff_discount_pct"].isna() | (df["eff_discount_pct"] == 0)
        df.loc[mask, "eff_discount_pct"] = rp[mask]
        df["rabata_pct"] = rp.map(_zero_small_discount)
    df["eff_discount_pct"] = df["eff_discount_pct"].fillna(Decimal("0"))
    df["eff_discount_pct"] = df["eff_discount_pct"].apply(_zero_small_discount)
    return df


def _backfill_discount_pct_from_prices(df: pd.DataFrame) -> pd.DataFrame:
    """
    Če procenta še nimamo, ga izračunamo iz cene pred/po rabatu:
        pct = (1 - cena_po / cena_pred) * 100
    Vzame prvi razpoložljivi par stolpcev (robustno preko
    `first_existing_series`).
    """

    if df is None or df.empty:
        return df

    # poskusi najti 'ceno pred' in 'ceno po' v različnih možnih imenih
    s_pred = first_existing_series(
        df, ["cena_bruto", "Neto pred rab.", "Neto pred rabatu", "cena_pred"]
    )
    s_po = first_existing_series(
        df, ["cena_po_rabatu", "cena_netto", "Neto po rab.", "Neto po rabatu"]
    )
    if s_pred is None or s_po is None:
        return df

    pred = s_pred.apply(_dec_or_zero)
    po = s_po.apply(_dec_or_zero)

    if "eff_discount_pct" not in df.columns:
        df["eff_discount_pct"] = Decimal("0")
    eff = df["eff_discount_pct"].apply(_dec_or_zero)

    # maska: tam kjer eff=0 in cena_pred > 0
    mask = (eff == 0) & (pred != 0)
    if bool(mask.any()):

        def _calc(p_before, p_after):
            try:
                pct = (
                    (p_before - p_after) / p_before * Decimal("100")
                ).quantize(Decimal("0.01"), ROUND_HALF_UP)
                return _zero_small_discount(pct)
            except Exception:
                return Decimal("0")

        df.loc[mask, "eff_discount_pct"] = [
            _calc(pb, pa) for pb, pa in zip(pred[mask], po[mask])
        ]
        if "rabata_pct" in df.columns:
            rp = df["rabata_pct"].apply(_dec_or_zero)
            df.loc[(rp == 0) & mask, "rabata_pct"] = df.loc[
                (rp == 0) & mask, "eff_discount_pct"
            ]
    return df


def _booked_mask_from(df_or_sr: pd.DataFrame | pd.Series) -> pd.Series:
    """True, če vrstica vsebuje dejansko knjiženo kodo."""

    excluded = _excluded_codes_upper()

    if isinstance(df_or_sr, pd.Series):
        sr = df_or_sr.astype("string").map(_norm_wsm_code)
        sr = sr.fillna("").str.upper()
        return sr.ne("") & ~sr.isin(excluded)

    if not isinstance(df_or_sr, pd.DataFrame):
        return pd.Series(dtype="bool")

    df = df_or_sr

    # Najprej poskusi z dejanskim knjiženjem (_summary_key/_booked_sifra).
    cols = ["_summary_key", "_booked_sifra", "WSM šifra", "wsm_sifra"]
    try:
        code_series = first_existing_series(df, cols)
    except Exception:
        code_series = None

    if code_series is not None:
        codes = code_series.astype("string").map(_norm_wsm_code)
        codes = codes.fillna("").str.upper()
        mask = codes.ne("") & ~codes.isin(excluded)
        return mask

    if "status" in df.columns:
        st = df["status"].fillna("").astype(str).str.upper().str.strip()
        return st.str.startswith(("POVEZANO", "AUTO"))

    return pd.Series(False, index=df.index)


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
    pct = _zero_small_discount(pct)
    pct = pct.quantize(DEC2, rounding=ROUND_HALF_UP)
    # manj občutljivo na drobne razlike: 3 decimalke
    ua3 = (unit_after if unit_after is not None else Decimal("0")).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )
    return (pct, ua3)


# Logger setup
log = logging.getLogger(__name__)
TRACE = getenv("WSM_TRACE", "0") not in {"0", "false", "False"}
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
        # uporabi efektivni rabat; če ga ni, vzemi surovega
        # (negativno ničlo sproti počistimo)
        pct = row.get("eff_discount_pct", row.get("rabata_pct", Decimal("0")))
        if not isinstance(pct, Decimal):
            try:
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
        pct = _zero_small_discount(pct)
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


def _sum_decimal(values) -> Decimal:
    """Return the Decimal sum of ``values``."""

    total = Decimal("0")
    for value in values:
        total += _as_dec(value, "0")
    return total


def _calculate_smart_tolerance(
    net_total: Decimal, invoice_gross: Decimal
) -> Decimal:
    """Return an adaptive tolerance based on invoice size."""

    base_amount = max(
        _as_dec(net_total, "0").copy_abs(),
        _as_dec(invoice_gross, "0").copy_abs(),
    )
    base_min = max(DEFAULT_TOLERANCE, Decimal("0.01"))
    small = max(base_min, TOLERANCE_BASE)
    if base_amount <= Decimal("100"):
        return small
    if base_amount <= Decimal("1000"):
        return max(small, Decimal("0.05"))
    if base_amount <= Decimal("10000"):
        return max(small, Decimal("0.10"))
    return max(small, Decimal("0.50"))


def _resolve_tolerance(net_total: Decimal, invoice_gross: Decimal) -> Decimal:
    """Determine the effective rounding tolerance for totals."""

    base_min = max(DEFAULT_TOLERANCE, Decimal("0.01"))
    tolerance = base_min
    if SMART_TOLERANCE_ENABLED:
        tolerance = max(
            tolerance, _calculate_smart_tolerance(net_total, invoice_gross)
        )
    max_limit = max(base_min, MAX_TOLERANCE)
    tolerance = min(tolerance, max_limit)
    tolerance = tolerance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    log.debug(
        "Tolerance calculation: net=%s, gross=%s, resolved=%s",
        net_total,
        invoice_gross,
        tolerance,
    )
    return tolerance


def _append_rounding_row(df: pd.DataFrame, difference: Decimal) -> pd.DataFrame:
    """Append a rounding correction row to ``df``."""

    diff_q = difference.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    row = {col: pd.NA for col in df.columns}
    row.update(
        {
            "sifra_dobavitelja": "_ROUND_",
            "naziv": "Zaokrožitev",
            "kolicina": Decimal("1"),
            "enota": "kos",
            "vrednost": diff_q,
            "rabata": Decimal("0"),
            "ddv": Decimal("0"),
            "ddv_stopnja": Decimal("0"),
        }
    )
    if "naziv_ckey" in df.columns:
        row["naziv_ckey"] = _clean("Zaokrožitev")
    if "status" in df.columns:
        row["status"] = "AUTO_CORRECTION"
    if "wsm_sifra" in df.columns:
        row["wsm_sifra"] = "OSTALO"
    if "WSM šifra" in df.columns:
        row["WSM šifra"] = "OSTALO"
    if "wsm_naziv" in df.columns:
        row["wsm_naziv"] = "Zaokrožitev"
    if "WSM Naziv" in df.columns:
        row["WSM Naziv"] = "Zaokrožitev"
    if "multiplier" in df.columns:
        row["multiplier"] = Decimal("1")
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def _maybe_apply_rounding_correction(
    df: pd.DataFrame,
    header_totals: dict[str, Decimal],
    doc_discount: Decimal,
) -> pd.DataFrame:
    """Add an automatic rounding row when the difference exceeds tolerance."""

    if not ROUNDING_CORRECTION_ENABLED:
        return df

    if any(
        column in df.columns
        and df[column].astype(str).eq(marker).any()
        for column, marker in (
            ("status", "AUTO_CORRECTION"),
            ("sifra_dobavitelja", "_ROUND_"),
        )
    ):
        log.debug("Rounding correction row already exists, skipping")
        return df

    doc_total = _as_dec(doc_discount, "0")
    line_total = _sum_decimal(df.get("vrednost", []))
    calc_net_total = line_total + doc_total
    invoice_gross = _as_dec(header_totals.get("gross"), calc_net_total)
    tolerance = _resolve_tolerance(calc_net_total, invoice_gross)
    header_net = _as_dec(header_totals.get("net"), "0")
    difference = header_net - calc_net_total
    if abs(difference) <= tolerance:
        return df

    df = _append_rounding_row(df, difference)
    log.info(
        "Dodana korekcijska vrstica za zaokroževanje: %s (toleranca %s)",
        f"{difference.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):+.2f} €",
        tolerance,
    )
    return df


def _clean_neg_zero(val):
    """Normalize Decimal('-0') or -0.00 to plain zero."""
    d = _as_dec(val, default="0")
    return d if d != 0 else _as_dec("0", default="0")


def classify_net_difference(
    header_net, computed_net, tolerance: Decimal = Decimal("0.05")
):
    """
    Vrne razvrstitev razlike med neto zneskom iz glave in izračunanim netom.

    * "ok"        → brez razlike
    * "rounding" → razlika je manjša ali enaka toleranci (zaokroževanje)
    * "mismatch" → razlika je večja od tolerance
    """

    if header_net is None or computed_net is None:
        return "ok"

    try:
        header = header_net if isinstance(header_net, Decimal) else Decimal(str(header_net))
        computed = (
            computed_net
            if isinstance(computed_net, Decimal)
            else Decimal(str(computed_net))
        )
    except Exception:
        return "ok"

    diff = (computed - header).copy_abs()

    if diff == 0:
        return "ok"
    if diff <= tolerance:
        return "rounding"
    return "mismatch"


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


def _apply_saved_multipliers(
    df: pd.DataFrame,
    links_df: pd.DataFrame,
    *,
    tree: ttk.Treeview | None = None,
    update_summary: Callable | None = None,
    update_totals: Callable | None = None,
) -> int:
    """Apply stored quantity multipliers to ``df``.

    Parameters
    ----------
    df:
        DataFrame with the current invoice rows.
    links_df:
        Saved mapping DataFrame containing the ``multiplier`` column.
    tree:
        Optional Treeview used to refresh the visual grid when adjustments are
        applied after the GUI is initialised.
    update_summary / update_totals:
        Optional callbacks that refresh aggregated information when provided.

    Returns
    -------
    int
        Number of rows for which a multiplier adjustment was applied.
    """

    if not isinstance(df, pd.DataFrame) or df.empty:
        return 0
    if not isinstance(links_df, pd.DataFrame) or links_df.empty:
        return 0
    if "sifra_dobavitelja" not in df.columns or "multiplier" not in links_df.columns:
        return 0
    required = {"kolicina_norm", "cena_po_rabatu", "cena_pred_rabatom"}
    if not required.issubset(df.columns):
        return 0

    def _strip_series(series: pd.Series) -> pd.Series:
        ser = series.astype("string").fillna("").replace("<NA>", "")
        return ser.str.strip()

    def _clean_series(series: pd.Series) -> pd.Series:
        ser = series.astype("string").fillna("").replace("<NA>", "")
        return ser.map(lambda val: _clean(val) if val else "")

    invoice_codes = _strip_series(df["sifra_dobavitelja"])
    if "naziv_ckey" in df.columns:
        invoice_names = _clean_series(df["naziv_ckey"])
    elif "naziv" in df.columns:
        invoice_names = _clean_series(df["naziv"])
    else:
        return 0

    link_df = links_df.copy()
    link_df["sifra_dobavitelja"] = _strip_series(link_df["sifra_dobavitelja"])
    if "naziv_ckey" in link_df.columns:
        link_df["naziv_ckey"] = _clean_series(link_df["naziv_ckey"])
    elif "naziv" in link_df.columns:
        link_df["naziv_ckey"] = _clean_series(link_df["naziv"])
    else:
        link_df["naziv_ckey"] = ""

    link_df = link_df.loc[
        (link_df["sifra_dobavitelja"] != "") & (link_df["naziv_ckey"] != "")
    ]
    if link_df.empty:
        return 0

    link_df = link_df.drop_duplicates(
        ["sifra_dobavitelja", "naziv_ckey"], keep="last"
    )
    link_idx = link_df.set_index(["sifra_dobavitelja", "naziv_ckey"])

    df_keys = list(zip(invoice_codes, invoice_names))
    try:
        matched = link_idx.reindex(df_keys)
    except Exception:
        matched = link_idx.loc[link_idx.index.intersection(df_keys)].reindex(df_keys)

    matched = matched.reset_index(drop=True)
    matched.index = df.index

    multipliers = matched.get("multiplier")
    if multipliers is None:
        return 0

    def _maybe_decimal(value) -> Decimal | None:
        if isinstance(value, Decimal):
            return value if value.is_finite() else None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        if value in (None, "", " "):
            return None
        try:
            candidate = Decimal(str(value))
            return candidate if candidate.is_finite() else None
        except Exception:
            return None

    saved = multipliers.map(_maybe_decimal)
    if saved is None:
        return 0

    actions: list[tuple[int, Decimal]] = []
    for idx, target in saved.items():
        if target is None or target <= 0:
            continue
        current_raw = df.at[idx, "multiplier"] if "multiplier" in df.columns else Decimal("1")
        current = _maybe_decimal(current_raw) or Decimal("1")
        if current == 0:
            continue
        if target == current:
            continue
        try:
            factor = target / current
        except Exception:
            continue
        if factor == 1:
            continue
        actions.append((idx, factor))

    if not actions:
        return 0

    log.info("Applying multipliers for %s rows", len(actions))
    applied = 0
    for idx, factor in actions:
        try:
            _apply_multiplier(
                df,
                idx,
                factor,
                tree=tree,
                update_summary=update_summary,
                update_totals=update_totals,
            )
            applied += 1
        except Exception as exc:
            log.warning(
                "Napaka pri samodejni uporabi množitelja za vrstico %s: %s",
                idx,
                exc,
            )
    return applied


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

    # Prepreči UnboundLocalError za 'Decimal' zaradi poznejših lokalnih
    # importov v tej funkciji ter poskrbi za Decimal util.
    from decimal import Decimal, ROUND_HALF_UP

    def _is_placeholder_supplier_name(
        value: str | None,
        vat_value: str | None,
        code_value: str | None = None,
    ) -> bool:
        normalized = (value or "").strip()
        if not normalized:
            return True
        lowered = normalized.casefold()
        if lowered in {"unknown", "neznano"}:
            return True
        vat_norm = _norm_vat(vat_value or "")
        if vat_norm:
            name_norm = _norm_vat(normalized)
            if name_norm and name_norm.casefold() == vat_norm.casefold():
                return True
        if code_value:
            code_norm = _norm_vat(code_value)
            name_norm = _norm_vat(normalized)
            if code_norm and name_norm and name_norm.casefold() == code_norm.casefold():
                return True
            if normalized.casefold() == str(code_value).strip().casefold():
                return True
        return False

    def _extract_supplier_name_from_nad(xml_path: Path) -> str | None:
        try:
            tree = LET.parse(xml_path, parser=XML_PARSER)
            root_el = tree.getroot()
        except Exception:
            return None

        try:
            groups = root_el.xpath(".//*[local-name()='G_SG2']")
        except Exception:
            groups = []

        for grp in groups:
            try:
                nad_nodes = grp.xpath("./*[local-name()='S_NAD']")
            except Exception:
                nad_nodes = []
            for nad in nad_nodes:
                try:
                    types = [
                        t.strip()
                        for t in nad.xpath("./*[local-name()='D_3035']/text()")
                        if t and t.strip()
                    ]
                except Exception:
                    types = []
                if types and types[0] not in {"SU", "SE"}:
                    continue
                try:
                    parts = [
                        p.strip()
                        for p in nad.xpath(
                            "./*[local-name()='C_C080']/*[local-name()='D_3036']/text()"
                        )
                        if p and p.strip()
                    ]
                except Exception:
                    parts = []
                if parts:
                    return " ".join(parts)
        return None

    log.info("=== ENVIRONMENT CHECK ===")
    log.info("AUTO_APPLY_LINKS = %s", AUTO_APPLY_LINKS)
    log.info(
        "AUTO_APPLY_LINKS raw (%s) = %s",
        AUTO_APPLY_LINKS_SOURCE,
        AUTO_APPLY_LINKS_RAW,
    )
    log.info("WSM_AUTO_APPLY_LINKS env = %s", os.getenv("WSM_AUTO_APPLY_LINKS"))
    log.info("AUTO_APPLY_LINKS env = %s", os.getenv("AUTO_APPLY_LINKS"))

    net_mismatch = bool(df.attrs.get("net_mismatch"))
    net_warning = bool(df.attrs.get("net_warning"))
    auto_apply_links = AUTO_APPLY_LINKS
    if net_mismatch and auto_apply_links:
        auto_apply_links = False
        log.info(
            "Samodejno ujemanje onemogočeno zaradi net_mismatch iz vhodnega df."
        )

    log.info(
        "AUTO_APPLY_LINKS=%s → shranjene povezave %s.",
        auto_apply_links,
        "BODO uveljavljene" if auto_apply_links else "NE bodo",
    )
    log.info("net_mismatch=%s, net_warning=%s", net_mismatch, net_warning)

    eslog_totals = SimpleNamespace(mode=df.attrs.get("mode"))
    log.info("ESLOG totals mode: %s", eslog_totals.mode)

    df = df.copy()
    df = df.loc[:, ~df.columns.duplicated()].copy()
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
    # Dobaviteljeva davčna številka iz XML
    supplier_code: str = ""
    supplier_code_xml: str = ""
    supplier_name_xml: str = ""
    supplier_vat_xml_norm: str | None = None
    # Try to extract supplier VAT directly from the invoice XML
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            code_raw, supplier_name_raw, supplier_vat_xml = get_supplier_info_vat(
                invoice_path
            )
            supplier_code_xml = (code_raw or "").strip()
            supplier_code_norm = _norm_vat(supplier_code_xml)
            supplier_vat_xml_norm = _norm_vat(supplier_vat_xml or "")
            supplier_code = (
                supplier_vat_xml_norm
                or (supplier_vat_xml or "").strip()
                or ""
            )
            if (
                not supplier_code
                and supplier_code_norm
                and supplier_code_xml.upper().startswith("SI")
            ):
                supplier_code = supplier_code_norm
            if not supplier_code:
                fallback_code = supplier_code_xml or (code_raw or "").strip()
                supplier_code = fallback_code or supplier_code_norm or ""
            if isinstance(supplier_code, str):
                supplier_code = supplier_code.strip()

            supplier_name_candidate = (supplier_name_raw or "").strip()
            if _is_placeholder_supplier_name(
                supplier_name_candidate,
                supplier_vat_xml_norm,
                supplier_code_norm or supplier_code_xml or code_raw,
            ):
                fallback_name = _extract_supplier_name_from_nad(invoice_path)
                if fallback_name:
                    supplier_name_candidate = fallback_name.strip()
            supplier_name_xml = supplier_name_candidate
            log.info("Supplier code extracted: %s", supplier_code)
        except Exception as exc:
            log.debug("Supplier code lookup failed: %s", exc)
    suppliers_file = links_file.parent.parent
    log.debug(f"Pot do mape links: {suppliers_file}")
    sup_map = _load_supplier_map(suppliers_file)

    log.info("Resolved supplier code: %s", supplier_code)
    supplier_info = sup_map.get(supplier_code, {})
    if not supplier_info and supplier_code_xml and supplier_code_xml != supplier_code:
        supplier_info = sup_map.get(supplier_code_xml, {})
    supplier_vat = supplier_info.get("vat")
    if supplier_vat_xml_norm:
        supplier_vat = supplier_vat_xml_norm

    service_date = None
    invoice_number = None
    if invoice_path:
        suffix = invoice_path.suffix.lower()
        if suffix == ".xml":
            try:
                service_date = extract_service_date(invoice_path)
                invoice_number = extract_invoice_number(invoice_path)
            except Exception as exc:
                log.warning(f"Napaka pri branju glave računa: {exc}")
        elif suffix == ".pdf":
            try:
                from wsm.parsing.pdf import (
                    extract_invoice_number as extract_invoice_number_pdf,
                    extract_service_date as extract_service_date_pdf,
                )

                service_date = extract_service_date_pdf(invoice_path)
                invoice_number = extract_invoice_number_pdf(invoice_path)
            except Exception as exc:
                log.warning(f"Napaka pri branju glave računa: {exc}")

    inv_name = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        if supplier_name_xml:
            inv_name = supplier_name_xml
        else:
            try:
                from wsm.parsing.eslog import get_supplier_name

                inv_name = get_supplier_name(invoice_path)
            except Exception:
                inv_name = None
    elif invoice_path and invoice_path.suffix.lower() == ".pdf":
        try:
            from wsm.parsing.pdf import get_supplier_name_from_pdf

            inv_name = get_supplier_name_from_pdf(invoice_path)
        except Exception:
            inv_name = None

    def _is_placeholder_name(value: str | None) -> bool:
        if not value:
            return True
        normalized = str(value).strip()
        if not normalized:
            return True
        lowered = normalized.casefold()
        if lowered in {"unknown", "neznano"}:
            return True
        compact = re.sub(r"[^0-9A-Za-z]", "", normalized)
        if compact.isdigit():
            return True
        if re.fullmatch(r"SI?\d{6,}", compact, re.IGNORECASE):
            return True
        if supplier_code and lowered == str(supplier_code).strip().casefold():
            return True
        vat_norm = _norm_vat(normalized)
        if vat_norm and vat_norm.casefold() == normalized.casefold():
            return True
        if vat_norm and vat_norm.casefold() == compact.casefold():
            return True
        return False


    full_supplier_name = (
        (supplier_info.get("ime") or inv_name or supplier_code or "")
        .strip()
    )

    if inv_name and _is_placeholder_name(full_supplier_name) and not _is_placeholder_name(inv_name):
        full_supplier_name = inv_name.strip()


    supplier_vat_norm = _norm_vat(supplier_vat or "")
    if not supplier_vat_norm:
        supplier_vat_norm = _norm_vat(supplier_code)
    supplier_vat = supplier_vat_norm or (supplier_vat if supplier_vat else None)

    vat_cf = supplier_vat.casefold() if isinstance(supplier_vat, str) else ""
    missing_name = not full_supplier_name
    if vat_cf and not missing_name:
        name_cf = full_supplier_name.casefold()
        missing_name = name_cf == vat_cf or name_cf in {"unknown", "neznano"}
    if vat_cf and missing_name:
        for info in sup_map.values():
            candidate_vat = _norm_vat(info.get("vat") or "")
            if candidate_vat and candidate_vat.casefold() == vat_cf:
                candidate_name = (info.get("ime") or "").strip()
                if candidate_name and candidate_name.casefold() not in {
                    vat_cf,
                    "unknown",
                    "neznano",
                }:
                    full_supplier_name = candidate_name
                    log.debug(
                        "Resolved supplier name %s from VAT %s",
                        candidate_name,
                        supplier_vat,
                    )
                    break

    if not full_supplier_name:
        full_supplier_name = supplier_code
    else:
        full_supplier_name = full_supplier_name.strip()
    supplier_name = short_supplier_name(full_supplier_name)

    log.debug(f"Supplier info: {supplier_info}")

    header_totals_meta: dict[str, Any] = {}
    header_result = _build_header_totals(
        invoice_path, invoice_total, invoice_gross, with_meta=True
    )
    if isinstance(header_result, tuple):
        header_totals, header_totals_meta = header_result
    else:
        header_totals = header_result

    log.info(
        "Header totals resolved: gross=%s (src=%s) net=%s (src=%s) vat=%s (src=%s)",
        header_totals.get("gross"),
        header_totals_meta.get("gross_source", "n/a"),
        header_totals.get("net"),
        header_totals_meta.get("net_source", "n/a"),
        header_totals.get("vat"),
        header_totals_meta.get("vat_source", "n/a"),
    )

    service_date = (
        service_date
        or header_totals.get("service_date")
        or supplier_info.get("service_date")
        or ""
    )

    try:
        manual_old = pd.read_excel(links_file, dtype=str)
        log.info("=== EXCEL BRANJE ===")
        log.info("Povezave naložene: %s vrstic", len(manual_old))
        log.info("Stolpci v Excel: %s", manual_old.columns.tolist())
        if "status" in manual_old.columns:
            povezano_count = (
                manual_old["status"].astype(str).str.upper() == "POVEZANO"
            ).sum()
            log.info("Vrstic s status=POVEZANO: %d", povezano_count)
            log.debug(
                "Primer status vrednosti: %s",
                manual_old["status"].head().tolist(),
            )
        else:
            log.warning("Excel nima stolpca 'status'!")

        if "wsm_sifra" in manual_old.columns:
            wsm_count = manual_old["wsm_sifra"].notna().sum()
            log.info("Vrstic z wsm_sifra: %d", wsm_count)

        log.debug(
            "Prvi 3 zapisi iz Excel:\n%s",
            manual_old.head(3).to_string(),
        )
        log.debug("Primer ročno shranjenih povezav:\n%s", manual_old.head())
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

    raw_supplier_names = [
        str(n).strip()
        for n in manual_old.get("dobavitelj", [])
        if isinstance(n, str)
    ]
    manual_supplier_names = [name for name in raw_supplier_names if name]


    def _is_placeholder_name(value: str | None) -> bool:
        if not value:
            return True
        normalized = str(value).strip()
        if not normalized:
            return True
        lowered = normalized.casefold()
        if lowered in {"unknown", "neznano"}:
            return True
        compact = re.sub(r"[^0-9A-Za-z]", "", normalized)
        if compact.isdigit():
            return True
        if re.fullmatch(r"SI?\d{6,}", compact, re.IGNORECASE):
            return True
        if supplier_code and lowered == str(supplier_code).strip().casefold():
            return True
        vat_norm = _norm_vat(normalized)
        if vat_norm and vat_norm.casefold() == normalized.casefold():
            return True
        if vat_norm and vat_norm.casefold() == compact.casefold():
            return True
        return False


    manual_full_name = next(
        (name for name in manual_supplier_names if not _is_placeholder_name(name)),
        "",
    )
    if manual_full_name and _is_placeholder_name(full_supplier_name):
        full_supplier_name = manual_full_name

    short_manual_names: list[str] = []
    for candidate in manual_supplier_names:
        short = short_supplier_name(candidate)
        if short and short not in short_manual_names:
            short_manual_names.append(short)

    short_from_full = short_supplier_name(full_supplier_name)
    if short_from_full:
        supplier_name = short_from_full

    if supplier_name and supplier_name not in short_manual_names:
        short_manual_names.insert(0, supplier_name)
    elif short_manual_names:
        supplier_name = short_manual_names[0]

    if not supplier_name:
        supplier_name = supplier_code

    if _is_placeholder_name(full_supplier_name):
        full_supplier_name = supplier_name or supplier_code

    df["dobavitelj"] = supplier_name
    log.debug(f"Supplier name nastavljen na: {supplier_name}")
    log.debug("Full supplier name after resolution: %s", full_supplier_name)
    log.info("Default name retrieved: %s", supplier_name)


    # Normalize codes before lookup
    df["sifra_dobavitelja"] = df["sifra_dobavitelja"].fillna("").astype(str)
    empty_sifra = df["sifra_dobavitelja"] == ""
    if empty_sifra.any():
        log.warning(
            "Prazne vrednosti v sifra_dobavitelja za "
            f"{empty_sifra.sum()} vrstic v df",
        )

    links_df = manual_old
    log.info(
        "Branje povezav iz: %s (exists=%s)",
        links_file,
        links_file.exists(),
    )
    df["naziv_ckey"] = df["naziv"].map(_clean)
    globals()["_PENDING_LINKS_DF"] = links_df
    log.info("Klic _apply_links_to_df with apply_codes=%s", auto_apply_links)
    df, auto_upd_cnt = _apply_links_to_df(
        df, links_df, apply_codes=auto_apply_links
    )
    # Shrani trenutno stanje mreže tako, da je na voljo tudi drugim handlerjem,
    # še preden morebitne kasnejše operacije dodatno prilagodijo DataFrame.
    globals()["_CURRENT_GRID_DF"] = df
    log.info("AUTO_APPLY_LINKS=%s", auto_apply_links)
    saved_status = df["status"].copy() if "status" in df.columns else None
    if auto_apply_links:
        try:
            df = _fill_names_from_catalog(df, wsm_df)
            df = _normalize_wsm_display_columns(df)
            globals()["_CURRENT_GRID_DF"] = df
            log.info(
                "Samodejno uveljavljene povezave: %d vrstic posodobljenih.",
                auto_upd_cnt,
            )
        except Exception as e:
            log.exception("Napaka pri auto-uveljavitvi povezav: %s", e)
        finally:
            if saved_status is not None:
                df["status"] = saved_status
    else:
        log.info(
            "AUTO_APPLY_LINKS=0 ali onemogočeno → shranjene povezave NE bodo "
            "uveljavljene samodejno.",
        )

    # Poskrbi za prisotnost in tipe stolpcev za WSM povezave
    for c in ("wsm_sifra", "wsm_naziv"):
        if c not in df.columns:
            df[c] = pd.Series(pd.NA, index=df.index, dtype="string")
        else:
            df[c] = df[c].astype("string")

    # Enotno ime prikaznih stolpcev v gridu (tudi ko AUTO_APPLY_LINKS=0)
    status_before_normalize = (
        df["status"].copy() if "status" in df.columns else None
    )
    df = _normalize_wsm_display_columns(df)
    if status_before_normalize is not None:
        df["status"] = status_before_normalize

    if not auto_apply_links:
        if "status" not in df.columns:
            df["status"] = ""
        mask_not_booked = df["status"].astype(str).str.upper().ne("POVEZANO")
        df.loc[mask_not_booked, ["wsm_sifra", "wsm_naziv"]] = pd.NA

    # Po morebitnem praznjenju ponovno poravnaj prikazne vrednosti
    status_before_second_normalize = (
        df["status"].copy() if "status" in df.columns else None
    )
    df = _normalize_wsm_display_columns(df)
    if status_before_second_normalize is not None:
        df["status"] = status_before_second_normalize

    df["multiplier"] = Decimal("1")
    log.debug(f"df po inicializaciji: {df.head().to_dict()}")

    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    # poskrbi, da je df_doc skladen z df
    df_doc = _normalize_wsm_display_columns(df_doc)
    df_doc = df_doc.loc[:, ~df_doc.columns.duplicated()].copy()

    doc_discount_raw = _sum_decimal(df_doc.get("vrednost", []))
    doc_discount = (
        doc_discount_raw
        if isinstance(doc_discount_raw, Decimal)
        else Decimal(str(doc_discount_raw))
    )
    log.debug("df before _DOC_ filter:\n%s", df.to_string())
    df = df[df["sifra_dobavitelja"] != "_DOC_"].copy()
    before_correction_len = len(df)
    df = _maybe_apply_rounding_correction(df, header_totals, doc_discount)
    correction_added = len(df) > before_correction_len
    if correction_added:
        doc_discount_raw = _sum_decimal(df_doc.get("vrednost", []))
        doc_discount = (
            doc_discount_raw
            if isinstance(doc_discount_raw, Decimal)
            else _as_dec(doc_discount_raw, "0")
        )
        df_doc = _normalize_wsm_display_columns(df_doc)
        df_doc = df_doc.loc[:, ~df_doc.columns.duplicated()].copy()
        effective_gross = (
            invoice_gross if invoice_gross is not None else header_totals.get("gross")
        )
        net_base_series = df.get("Skupna neto", df.get("vrednost", []))
        header_totals = _build_header_totals(
            invoice_path,
            _sum_decimal(net_base_series) + _as_dec(doc_discount, "0"),
            effective_gross,
        )
    # VAT values must stay as-is (net amounts are already without VAT)
    if "ddv" not in df.columns:
        df["ddv"] = Decimal("0")
    df["ddv"] = df["ddv"].apply(lambda x: _as_dec(x, "0"))
    # Ensure a clean sequential index so Treeview item IDs are predictable
    df = df.reset_index(drop=True)
    # Enotni vir resnice za neto/bruto zneske
    if "Skupna neto" in df.columns:
        df["total_net"] = df["Skupna neto"].apply(lambda x: _as_dec(x, "0"))
    else:
        df["total_net"] = df["vrednost"].apply(lambda x: _as_dec(x, "0"))

    # raw znesek pred rabatom – robustno tudi, če dobimo samo total_net
    if "rabata" not in df.columns:
        df["rabata"] = Decimal("0")
    df["rabata"] = df["rabata"].apply(lambda x: _as_dec(x, "0"))
    df["total_raw"] = (df["total_net"] + df["rabata"]).apply(lambda x: _as_dec(x, "0"))
    df["total_gross"] = (df["total_net"] + df["ddv"]).apply(
        lambda x: _as_dec(x, "0")
    )
    for _c in ("vrednost", "rabata", "Skupna neto"):
        if _c in df.columns:
            df[_c] = df[_c].apply(lambda x: _as_dec(x, "0"))
    df["cena_pred_rabatom"] = df.apply(
        lambda r: (
            r["total_raw"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    df["cena_po_rabatu"] = df.apply(
        lambda r: (
            r["total_net"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    df["rabata_pct"] = df.apply(
        lambda r: (
            (r["rabata"] / r["total_raw"] * Decimal("100")).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            if r["total_raw"] != 0
            else Decimal("0")
        ),
        axis=1,
    )
    df["is_gratis"] = df["rabata_pct"] >= Decimal("99.9")

    def _normalize_override_column() -> None:
        if "override_unit" not in df.columns:
            df["override_unit"] = pd.Series(pd.NA, index=df.index, dtype="string")
            return
        series = df["override_unit"]
        try:
            series = series.astype("string")
        except Exception:
            series = series.astype(str)
        series = series.replace({"<NA>": ""}).fillna("").str.strip()
        df["override_unit"] = series.replace("", pd.NA).astype("string")

    def _override_value(idx: int) -> str | None:
        if "override_unit" not in df.columns:
            return None
        val = df.at[idx, "override_unit"]
        try:
            if pd.isna(val):
                return None
        except Exception:
            if val is None:
                return None
        text = str(val).strip()
        return text or None

    def _recalculate_units() -> None:
        quantities: list[Decimal] = []
        units: list[str] = []
        for idx in df.index:
            raw_qty = df.at[idx, "kolicina"] if "kolicina" in df.columns else Decimal("0")
            qty_dec = raw_qty if isinstance(raw_qty, Decimal) else _as_dec(raw_qty, "0")
            raw_unit = df.at[idx, "enota"] if "enota" in df.columns else ""
            name_val = df.at[idx, "naziv"] if "naziv" in df.columns else ""
            vat_val = df.at[idx, "ddv_stopnja"] if "ddv_stopnja" in df.columns else None
            code_val = df.at[idx, "sifra_artikla"] if "sifra_artikla" in df.columns else None
            override_val = _override_value(idx)
            qty_norm, unit_norm = _norm_unit(
                qty_dec,
                raw_unit,
                name_val,
                vat_val,
                code_val,
                override_unit=override_val,
            )
            quantities.append(qty_norm)
            units.append(unit_norm)
        if len(df.index) == len(quantities):
            df.loc[:, "kolicina_norm"] = quantities
            df.loc[:, "enota_norm"] = units
        else:
            df["kolicina_norm"] = quantities
            df["enota_norm"] = units

    _normalize_override_column()
    _recalculate_units()
    # Keep ``kolicina_norm`` as ``Decimal`` to avoid losing precision in
    # subsequent calculations and when saving the file. Previously the column
    # was cast to ``float`` which could introduce rounding errors.
    df["warning"] = pd.NA

    # --- Lep opis rabata za prikaz v mreži ---
    def _q2(x):
        try:
            return (x if isinstance(x, Decimal) else Decimal(str(x))).quantize(
                DEC2, ROUND_HALF_UP
            )
        except Exception:
            return Decimal("0.00")

    def _fmt_eur(x: Decimal) -> str:
        return f"{_q2(x):.2f}"

    def _rab_opis(row) -> str:
        if bool(row.get("is_gratis")) or (
            _q2(row.get("rabata_pct", 0)) >= Decimal("99.90")
        ):
            return "100 % (GRATIS)"
        pct = _q2(row.get("rabata_pct", 0))
        if pct > 0:
            amt = _q2(row.get("rabata", 0))
            return f"{pct:.2f} % (−{_fmt_eur(amt)} €)"
        return ""

    df["rabat_opis"] = df.apply(_rab_opis, axis=1).astype("string")

    log.debug("df po normalizaciji: %s", df.head().to_dict())
    # Ensure 'multiplier' is a sane Decimal for later comparisons/UI
    if "multiplier" not in df.columns:
        df["multiplier"] = Decimal("1")
    else:
        df["multiplier"] = df["multiplier"].map(lambda v: _as_dec(v, "1"))

    _apply_saved_multipliers(df, links_df)

    # Naj povzetek in UI handlerji vedno uporabljajo zadnjo verzijo df
    globals()["_CURRENT_GRID_DF"] = df
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

    # STEP0.5: skrij "glavine" vrstice iz eSLOG-a (npr. "Dobavnica: ..."),
    # ki imajo 0 količine in 0 vrednosti – te niso dejanski artikli,
    # ampak samo dokumentacijske postavke.
    # Možno izklopiti z WSM_HIDE_HEADER_LINES=0
    if environ.get("WSM_HIDE_HEADER_LINES", "1") != "0":
        try:
            _mask_hdr = _mask_header_like_rows(df)
            if _mask_hdr.any():
                removed = int(_mask_hdr.sum())
                examples = (
                    df.loc[_mask_hdr, "naziv"].head(2).tolist()
                    if "naziv" in df.columns
                    else []
                )
                df = df.loc[~_mask_hdr].reset_index(drop=True).copy()
                _t(
                    "STEP0.5 hidden header-like rows: %d (e.g. %s)",
                    removed,
                    examples,
                )
        except Exception:
            pass

    # (premaknjeno) opozorila bomo preračunali po združevanju

    # 1) obvezno: zagotovimo eff_discount_pct še pred merge
    df = _ensure_eff_discount_pct(df)
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
    # 1a) če procenta še vedno ni, ga izračunamo iz cen pred/po
    before_backfill = (
        df["eff_discount_pct"].apply(_dec_or_zero)
        if "eff_discount_pct" in df.columns
        else None
    )
    df = _backfill_discount_pct_from_prices(df)
    if before_backfill is not None:
        after_backfill = df["eff_discount_pct"].apply(_dec_or_zero)
        changed = int(((before_backfill == 0) & (after_backfill != 0)).sum())
        _t("STEP1a backfilled discount pct from prices for %d rows", changed)

    # 1b) stolpec 'Rabat (%)' uporablja rabata_pct
    #     -> poravnaj iz eff_discount_pct
    try:
        if "eff_discount_pct" in df.columns:
            eff = df["eff_discount_pct"].apply(_dec_or_zero)
            if "rabata_pct" not in df.columns:
                df["rabata_pct"] = eff
                _t(
                    "STEP1b rabata_pct created from "
                    "eff_discount_pct for all rows"
                )
            else:
                rp = df["rabata_pct"].apply(_dec_or_zero)
                mask_sync = (rp == 0) & (eff != 0)
                if bool(mask_sync.any()):
                    df.loc[mask_sync, "rabata_pct"] = eff[mask_sync]
                    _t(
                        "STEP1b rabata_pct synced from "
                        "eff_discount_pct for %d rows",
                        int(mask_sync.sum()),
                    )
    except Exception as _e:
        _t("STEP1b sync skipped: %s", _e)

    # 1c) po sinhronizaciji ponovno zgradi prikazni opis rabata
    try:
        df["rabat_opis"] = df.apply(_rab_opis, axis=1).astype("string")
        _t("STEP1c rabat_opis rebuilt after pct sync")
    except Exception as _e:
        _t("STEP1c rabat_opis rebuild skipped: %s", _e)

    # Označi GRATIS vrstice (količina > 0 in neto = 0), da se ne izgubijo

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
            d = d.quantize(DEC2, rounding=ROUND_HALF_UP)
            d = _zero_small_discount(d)
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

    if getenv("WSM_DEBUG_BUCKET") == "1":
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

    # -- po združevanju posodobi imena in prikazne stolpce za GRID --
    df = _fill_names_from_catalog(df, wsm_df)

    # poskrbi za prikazne stolpce (vedno prepiši iz baznih)
    if "WSM šifra" not in df.columns:
        df["WSM šifra"] = ""
    if "WSM Naziv" not in df.columns:
        df["WSM Naziv"] = ""
    df["WSM šifra"] = df.get("wsm_sifra").astype("string").fillna("")
    df["WSM Naziv"] = df.get("wsm_naziv").astype("string").fillna("")

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

    def _build_wsm_summary(
        df_all: pd.DataFrame, hdr_net_total: Decimal | None
    ) -> tuple[pd.DataFrame, str, Decimal | None]:
        """
        Zgradi POVZETEK po WSM šifrah + OSTALO, da se vsota ujema z glavo.

        Vrne:
          - summary_df: DataFrame v formatu, ki ga pričakuje _render_summary
          - status: "" / "Δ" / "X"
          - net_diff: razlika header_net - grid_net (ali None, če header_net ni znan)
        """
        if df_all is None or df_all.empty:
            return summary_df_from_records([]), "", None

        # ---- 1) Učinkovita WSM koda po vrstici (_booked_sifra > wsm_sifra > WSM šifra) ----
        code_s = first_existing_series(
            df_all,
            ["_booked_sifra", "wsm_sifra", "WSM šifra"],
        )
        if code_s is None:
            code_s = pd.Series([""] * len(df_all), index=df_all.index)

        code_s = code_s.astype("string").fillna("").str.strip()

        # izloči "izključene" kode – te gredo pod OSTALO
        excl_fn = globals().get("_excluded_codes_upper")
        excluded = excl_fn() if callable(excl_fn) else frozenset()
        code_upper = code_s.str.upper()

        is_booked = code_s.ne("") & ~code_upper.isin(excluded)

        # vse, kar ni "prava" WSM šifra, gre pod OSTALO
        eff_code = code_s.where(is_booked, "OSTALO")

        # ---- 2) količina, znesek, rabat, naziv ----
        qty_s = first_existing_series(
            df_all, ["kolicina_norm", "Količina"], fill_value=Decimal("0")
        )

        # uporabi enotni total_net za seštevke; vrednost je le prikazna
        amount_raw_s = first_existing_series(
            df_all,
            ["total_raw", "vrednost", "total_net", "Skupna neto"],
            fill_value=Decimal("0"),
        )
        amount_discounted_s = first_existing_series(
            df_all,
            ["total_net", "Skupna neto", "Neto po rabatu", "vrednost"],
            fill_value=Decimal("0"),
        )
        if amount_discounted_s is None:
            amount_discounted_s = pd.Series([Decimal("0")] * len(df_all), index=df_all.index)

        rab_s = first_existing_series(
            df_all, ["rabata_pct", "eff_discount_pct"], fill_value=Decimal("0")
        )

        name_s = first_existing_series(
            df_all, ["WSM Naziv", "WSM naziv", "wsm_naziv"]
        )
        if name_s is None:
            name_s = pd.Series([""] * len(df_all), index=df_all.index, dtype="string")
        name_s = name_s.astype("string").fillna("")

        work = pd.DataFrame(
            {
                "code": eff_code,
                "qty": qty_s,
                "net_raw": amount_raw_s,
                "net_discounted": amount_discounted_s,
                "rabat": rab_s,
                "name": name_s,
            }
        )

        # ---- 3) grupiranje po kodi (WSM šifra + OSTALO) ----
        records: list[dict[str, object]] = []

        for code, g in work.groupby("code", dropna=False):
            # varno seštevanje kot Decimal
            def _dsum(series):
                total = Decimal("0")
                for v in series:
                    if v in (None, ""):
                        continue
                    try:
                        total += v if isinstance(v, Decimal) else Decimal(str(v))
                    except Exception:
                        continue
                return total

            net_total_raw = _dsum(g["net_raw"])
            net_total_discounted = _dsum(g["net_discounted"])
            qty_total = _dsum(g["qty"])

            rab_val = g["rabat"].iloc[0]
            if not isinstance(rab_val, Decimal):
                try:
                    rab_val = Decimal(str(rab_val))
                except Exception:
                    rab_val = Decimal("0")

            if code == "OSTALO":
                disp_code = ""
                disp_name = "OSTALO (brez WSM šifre)"
            else:
                disp_code = str(code)
                nm = g["name"].astype(str).str.strip()
                nm = nm[nm != ""]
                disp_name = nm.iloc[0] if len(nm) else disp_code

            records.append(
                {
                    "WSM šifra": disp_code,
                    "WSM Naziv": disp_name,
                    "Količina": qty_total,
                    "Znesek": net_total_raw,
                    "Rabat (%)": rab_val,
                    "Neto po rabatu": net_total_discounted,
                }
            )

        doc_disc = _as_dec(doc_discount, "0")
        if doc_disc != 0:
            records.append(
                {
                    "WSM šifra": "",
                    "WSM Naziv": "DOKUMENTARNI POPUST",
                    "Količina": Decimal("0"),
                    "Znesek": doc_disc,
                    "Rabat (%)": Decimal("0"),
                    "Neto po rabatu": doc_disc,
                }
            )

        summary_df = summary_df_from_records(records)

        # ---- 4) status X / Δ glede na header_totals["net"] ----
        doc_disc = _as_dec(doc_discount, "0")
        net_series = (
            amount_discounted_s
            if amount_discounted_s is not None
            else pd.Series([], dtype=object)
        )
        vat_series = df_all.get("ddv", pd.Series([], dtype=object))
        grid_net_total = (_sum_decimal(net_series) + doc_disc).quantize(
            Decimal("0.01")
        )
        grid_vat_total = _sum_decimal(vat_series).quantize(Decimal("0.01"))
        grid_gross_total = (grid_net_total + grid_vat_total).quantize(
            Decimal("0.01")
        )

        try:
            hdr_net = (
                Decimal(str(hdr_net_total))
                if hdr_net_total is not None
                else None
            )
        except Exception:
            hdr_net = None
        try:
            hdr_gross = (
                Decimal(str(header_totals.get("gross")))
                if header_totals.get("gross") is not None
                else None
            )
        except Exception:
            hdr_gross = None
        net_diff: Decimal | None = None
        tolerance_rounding = Decimal("0.05")

        if hdr_net is None:
            status = ""
        else:
            net_diff = (hdr_net - grid_net_total).quantize(Decimal("0.01"))
            log.info(
                "NET DIFF CHECK: doc_net=%s, grid_net=%s, diff=%s",
                hdr_net,
                grid_net_total,
                net_diff,
            )
            if abs(net_diff) > tolerance_rounding:
                status = "X"
            elif abs(net_diff) > Decimal("0.00"):
                status = "Δ"
            else:
                status = ""

        if hdr_gross is not None:
            gross_diff = (hdr_gross - grid_gross_total).quantize(Decimal("0.01"))
            log.info(
                "GROSS DIFF CHECK: doc_gross=%s, grid_gross=%s, diff=%s",
                hdr_gross,
                grid_gross_total,
                gross_diff,
            )

        return summary_df, status, net_diff

    # Po združevanju lahko 'rabat_opis' izgine – ga ponovno zgradimo.
    try:
        if "rabata_pct" in df.columns:
            df["rabat_opis"] = df.apply(_rab_opis, axis=1).astype("string")
            _t("STEP5c rabat_opis rebuilt after merge")
        else:
            # V skrajnem primeru zagotovi prazen stolpec, da grid ne pade
            if "rabat_opis" not in df.columns:
                df["rabat_opis"] = ""
                _t("STEP5c rabat_opis added as empty (rabata_pct missing)")
    except Exception as _e:
        _t(f"STEP5c rabat_opis rebuild skipped: {_e}")

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
        #  - če je OSTALO: šifra prazna, naziv "Ostalo"
        #  - sicer: šifra = _summary_key, naziv = wsm_naziv
        def _disp_sifra(r):
            k = str(r.get("_summary_key", "") or "")
            return "" if k == "OSTALO" else k

        def _disp_naziv(r):
            k = str(r.get("_summary_key", "") or "")
            if k == "OSTALO":
                return "Ostalo"
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

    try:
        if {"status", "wsm_sifra"}.issubset(df.columns):
            status_series = (
                df["status"].astype("string").fillna("").str.strip().str.upper()
            )
            codes_series = (
                df["wsm_sifra"].map(_norm_wsm_code).astype("string").fillna("").str.strip()
            )
            linked_mask = status_series.str.startswith("POVEZANO") & codes_series.ne("")
            if linked_mask.any():
                df.loc[linked_mask, "_booked_sifra"] = codes_series.loc[linked_mask]
                df.loc[linked_mask, "_summary_key"] = codes_series.loc[linked_mask]
                if "WSM šifra" in df.columns:
                    df.loc[linked_mask, "WSM šifra"] = codes_series.loc[linked_mask]
                if "wsm_naziv" in df.columns:
                    display_names = (
                        df.loc[linked_mask, "wsm_naziv"].astype("string").fillna("")
                    )
                    for col in ("WSM Naziv", "WSM naziv"):
                        if col in df.columns:
                            df.loc[linked_mask, col] = display_names
    except Exception as exc:  # pragma: no cover - defensive sync
        log.debug("Initial linked status sync skipped: %s", exc)

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
    net_total = (net_total + _as_dec(doc_discount, "0")).quantize(Decimal("0.01"))

    # header_net_dec izračunamo enkrat in ga uporabimo tudi v povzetku
    try:
        header_net_dec = (
            header_totals.get("net")
            if isinstance(header_totals.get("net"), Decimal)
            else Decimal(str(header_totals.get("net")))
        )
    except Exception:
        header_net_dec = None

    summary_totals: dict[str, Decimal] = {}

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

    # Initialize window title; it will be updated once the header widgets
    # are created.
    root.title("Ročna revizija")
    root.supplier_name = full_supplier_name
    root.supplier_code = supplier_code
    root.service_date = service_date

    if net_mismatch:
        try:
            messagebox.showerror(
                "Neto znesek",
                "Razlika v neto znesku – samodejno ujemanje je onemogočeno.",
            )
        except Exception as exc:
            log.warning("Prikaz opozorila o neto razliki ni uspel: %s", exc)

    closing = False
    _after_totals_id: str | None = None
    bindings: list[tuple[tk.Misc, str]] = []
    price_tip: tk.Toplevel | None = None
    last_warn_item: str | None = None
    status_tip: tk.Toplevel | None = None
    net_icon_label_holder: dict[str, tk.Widget | None] = {"widget": None}

    # Determine how many rows can fit based on the screen height. Roughly
    # 500px is taken by the header, summary and button sections so we convert
    # the remaining space to a row count assuming ~20px per row.
    screen_height = root.winfo_screenheight()
    # The invoice lines Treeview easily dominates the window on tall screens, so
    # cap its height to keep the footer/input area visible.
    tree_height = min(15, max(10, (screen_height - 500) // 20))
    # Start maximized but keep the window decorations visible
    try:
        root.state("zoomed")
    except tk.TclError:
        pass

    # Supplier metadata for the GUI header and info labels

    def format_date(d: str) -> str:
        try:
            digits = "".join(ch for ch in d if ch.isdigit())
            y, m, day = digits[:4], digits[4:6], digits[6:8]
            return f"{int(day)}.{int(m)}.{y}"
        except:
            return d

    vat_display = supplier_code or ""
    date_display = format_date(str(service_date)) if service_date else ""
    invoice_display = str(invoice_number).strip() if invoice_number else ""

    lines = [vat_display]
    if date_display or invoice_display:
        lines.append(" – ".join(p for p in (date_display, invoice_display) if p))

    header_var = tk.StringVar()
    header_var.set("\n".join(lines))
    root.title(f"Ročna revizija – {vat_display}")

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
        if invoice_display:
            _copy_to_clipboard(invoice_display)

    tk.Button(
        info_frame,
        text="Kopiraj številko računa",
        command=copy_invoice_number,
    ).grid(row=1, column=2, sticky="w", padx=(0, 4))

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
        "rabat_opis",
        "status",
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
        "Rabat",
        "Status",
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

    # Poravnava prikaznih in internih WSM stolpcev, da povzetek šteje pravilno
    def _sync_wsm_cols_local():
        try:
            # 1) Poravnaj kodo: če se prikazna "WSM šifra" razlikuje
            #    od interne "wsm_sifra", prepiši interno
            if "wsm_sifra" in df.columns and "WSM šifra" in df.columns:
                src = df["WSM šifra"].astype("string")
                cur = df["wsm_sifra"].astype("string")
                diff = cur.ne(src)
                if bool(diff.any()):
                    df.loc[diff, "wsm_sifra"] = src[diff]

            # 2) Poravnaj naziv: če se "WSM Naziv" razlikuje od "wsm_naziv",
            #    prepiši interno
            if "wsm_naziv" in df.columns and "WSM Naziv" in df.columns:
                srcn = df["WSM Naziv"].astype("string")
                curn = df["wsm_naziv"].astype("string")
                diffn = curn.ne(srcn)
                if bool(diffn.any()):
                    df.loc[diffn, "wsm_naziv"] = srcn[diffn]

            # 3) Če je vnešena koda, naziv pa manjka ali je "Ostalo"
            #    → backfill iz kataloga
            if {"wsm_sifra", "wsm_naziv"}.issubset(df.columns):
                has_code = (
                    df["wsm_sifra"]
                    .astype("string")
                    .fillna("")
                    .str.strip()
                    .ne("")
                )
                _names = (
                    df["wsm_naziv"].astype("string").fillna("").str.strip()
                )
                no_name = _names.eq("") | _names.str.lower().eq("ostalo")
                mask = has_code & no_name
                if bool(mask.any()):
                    sdf = globals().get("sifre_df") or globals().get("wsm_df")
                    if sdf is not None and {"wsm_sifra", "wsm_naziv"}.issubset(
                        sdf.columns
                    ):
                        name_map = (
                            sdf.assign(
                                wsm_sifra=sdf["wsm_sifra"]
                                .astype(str)
                                .str.strip(),
                                wsm_naziv=sdf["wsm_naziv"].astype(str),
                            )
                            .dropna(subset=["wsm_naziv"])
                            .drop_duplicates("wsm_sifra")
                            .set_index("wsm_sifra")["wsm_naziv"]
                        )
                        filled = (
                            df.loc[mask, "wsm_sifra"]
                            .astype(str)
                            .str.strip()
                            .map(name_map)
                        )
                        df.loc[mask, "wsm_naziv"] = filled
                        if "WSM Naziv" in df.columns:
                            df.loc[mask, "WSM Naziv"] = filled.fillna("")
        except Exception as _e:
            _t(f"_sync_wsm_cols_local skipped: {_e}")

    def _refresh_summary_ui():
        # poravnaj WSM stolpce (display → internal)
        _sync_wsm_cols_local()
        try:
            # Če je vnešena koda, posodobi '_booked_sifra' in status,
            # ter po potrebi napolni naziv iz kataloga
            if "wsm_sifra" in df.columns:
                cur = df["wsm_sifra"].astype("string").fillna("")

                booked = (
                    df["_booked_sifra"].astype("string").fillna("")
                    if "_booked_sifra" in df.columns
                    else pd.Series([""] * len(df), index=df.index)
                )
                st = (
                    df["status"].astype("string").fillna("")
                    if "status" in df.columns
                    else pd.Series([""] * len(df), index=df.index)
                )

                filled = cur.str.strip().ne("")
                empty_status = st.str.strip().eq("")
                changed_code = cur.ne(booked)
                mask = filled & (empty_status | changed_code)

                if bool(mask.any()):
                    normalized = cur[mask].map(_coerce_booked_code)
                    df.loc[mask, "_booked_sifra"] = normalized
                    if "_summary_key" in df.columns:
                        df.loc[mask, "_summary_key"] = normalized
                    if "status" in df.columns:
                        df.loc[mask, "status"] = "POVEZANO • ročno"
                        for idx in df.index[mask]:
                            rid = str(idx)
                            if tree.exists(rid) and _tree_has_col("status"):
                                v = _first_scalar(df.at[idx, "status"])
                                tree.set(
                                    rid,
                                    "status",
                                    "" if v is None or pd.isna(v) else str(v),
                                )

                    # Backfill naziva ob zamenjani kodi
                    # (ali če je bil prazen/"Ostalo")
                    try:
                        sdf = globals().get("sifre_df") or globals().get(
                            "wsm_df"
                        )
                        if sdf is not None and {
                            "wsm_sifra",
                            "wsm_naziv",
                        }.issubset(sdf.columns):
                            name_map = (
                                sdf.assign(
                                    wsm_sifra=sdf["wsm_sifra"]
                                    .astype(str)
                                    .str.strip(),
                                    wsm_naziv=sdf["wsm_naziv"].astype(str),
                                )
                                .dropna(subset=["wsm_naziv"])
                                .drop_duplicates("wsm_sifra")
                                .set_index("wsm_sifra")["wsm_naziv"]
                            )
                            fill = (
                                cur[mask].astype(str).str.strip().map(name_map)
                            )

                            if "wsm_naziv" in df.columns:
                                oldn = (
                                    df.loc[mask, "wsm_naziv"]
                                    .astype("string")
                                    .fillna("")
                                    .str.strip()
                                )
                                need = (
                                    oldn.eq("")
                                    | oldn.str.lower().eq("ostalo")
                                    | changed_code.loc[mask]
                                )
                                idx = need[need].index
                                df.loc[idx, "wsm_naziv"] = fill.reindex(
                                    idx
                                ).fillna(df.loc[idx, "wsm_naziv"])

                            if "WSM Naziv" in df.columns:
                                oldd = (
                                    df.loc[mask, "WSM Naziv"]
                                    .astype("string")
                                    .fillna("")
                                    .str.strip()
                                )
                                needd = (
                                    oldd.eq("")
                                    | oldd.str.lower().eq("ostalo")
                                    | changed_code.loc[mask]
                                )
                                idx2 = needd[needd].index
                                df.loc[idx2, "WSM Naziv"] = fill.reindex(
                                    idx2
                                ).fillna(df.loc[idx2, "WSM Naziv"])
                    except Exception as _e:
                        _t(
                            "catalog name backfill on code change failed: "
                            f"{_e}"
                        )
        except Exception as _e:
            _t(f"_refresh_summary_ui status sync skipped: {_e}")

        globals()["_CURRENT_GRID_DF"] = df
        try:
            _update_summary()
            _schedule_totals()
        except Exception as _e:
            _t(f"_refresh_summary_ui refresh skipped: {_e}")
        try:
            tree.focus_set()
        except Exception:
            pass

    # --- ENTER handlers: commit + close + clear + refresh summary ---
    def _on_combobox_return(event):
        try:
            # 0) commit kot FocusOut (zanesljivo zapiše vrednosti v df)
            _editor_focus_out(event)
        except Exception:
            pass
        try:
            # 1) sproži še <<ComboboxSelected>> (za obstoječe handlerje)
            event.widget.event_generate("<<ComboboxSelected>>")
        except Exception:
            pass
        try:
            # 2) zapri dropdown/popup (če je odprt)
            event.widget.event_generate("<Escape>")
        except Exception:
            pass
        try:
            # 3) počisti vnos in izbiro po idle (ne prepiši Tk handlerja)
            w = event.widget

            def _clear_after():
                try:
                    # odstrani izbrani index v listi in pobriši text
                    if hasattr(w, "current"):
                        w.current(-1)
                except Exception:
                    pass
                try:
                    w.set("")
                except Exception:
                    pass

            try:
                root.after_idle(_clear_after)
            except Exception:
                _clear_after()
        except Exception:
            pass
        try:
            # 4) osveži povzetek + fokus v grid
            _refresh_summary_ui()
        except Exception:
            pass
        return "break"

    def _on_entry_return(event):
        try:
            _editor_focus_out(event)
            _refresh_summary_ui()
        except Exception:
            pass
        return "break"

    for c, h in zip(cols, heads):
        tree.heading(c, text=h)
        width = (
            300
            if c == "naziv"
            else (
                80
                if c == "enota_norm"
                else (
                    160
                    if c == "warning"
                    else 140 if c == "rabat_opis" else 120
                )
            )
        )
        tree.column(c, width=width, anchor="w")

    def _tree_has_col(name: str) -> bool:
        """Ali ima Treeview stolpec z danim ID?

        Prepreči ``set()`` na neobstoječ stolpec.
        """
        try:
            return name in set(tree["columns"])
        except Exception:
            return False

    # ENTER naj deluje enako v vseh editorjih
    try:
        root.bind_class("Combobox", "<Return>", _on_combobox_return, add="+")
        root.bind_class("TCombobox", "<Return>", _on_combobox_return, add="+")
        root.bind_class("Entry", "<Return>", _on_entry_return, add="+")
        root.bind_class("TEntry", "<Return>", _on_entry_return, add="+")
        # Numpad Enter
        root.bind_class("Combobox", "<KP_Enter>", _on_combobox_return, add="+")
        root.bind_class(
            "TCombobox", "<KP_Enter>", _on_combobox_return, add="+"
        )
        root.bind_class("Entry", "<KP_Enter>", _on_entry_return, add="+")
        root.bind_class("TEntry", "<KP_Enter>", _on_entry_return, add="+")
        # Miškina izbira naj tudi osveži povzetek/sync

        def _on_combobox_selected(event):
            try:
                # 0) najprej zanesljivo zapiši vrednost (kot pri Enter)
                try:
                    _editor_focus_out(event)
                except Exception:
                    pass
                # 1) posodobi in tudi "pozabi" izbiro,
                #    da naslednje tipkanje začne na prazno
                _refresh_summary_ui()
                w = event.widget

                def _clear_after_sel():
                    try:
                        if hasattr(w, "current"):
                            w.current(-1)
                        # pobriši tudi vnosno polje
                        if hasattr(w, "set"):
                            w.set("")
                    except Exception:
                        pass

                try:
                    root.after_idle(_clear_after_sel)
                except Exception:
                    _clear_after_sel()
            except Exception:
                pass

        root.bind_class(
            "Combobox",
            "<<ComboboxSelected>>",
            _on_combobox_selected,
            add="+",
        )
        root.bind_class(
            "TCombobox",
            "<<ComboboxSelected>>",
            _on_combobox_selected,
            add="+",
        )
    except Exception:
        pass

    def _safe_get(row, col, default=""):
        try:
            return row.get(col, default)
        except Exception:
            return default

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
        if bool(row.get("_never_booked", False)):
            row_tags.append("unbooked")

        status_val = str(row.get("status", "") or "").strip().upper()
        summary_val = str(row.get("_summary_key", "") or "").strip().upper()
        if status_val.startswith("POVEZANO") or summary_val not in {"", "OSTALO"}:
            if "unbooked" in row_tags:
                row_tags.remove("unbooked")
            if "linked" not in row_tags:
                row_tags.append("linked")

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
    summary_frame.pack(fill="x", pady=(6, 2))
    tk.Label(
        summary_frame,
        text="Povzetek po WSM šifrah",
        font=("Arial", 12, "bold"),
    ).pack()

    # Levi info-panel za povzetek – ustvarjen enkrat, nato osvežujemo StringVar
    summary_info_frame = tk.Frame(summary_frame)
    summary_info_frame.pack(side="left", fill="y", padx=(0, 10))
    sum_booked_var = tk.StringVar()
    sum_unbooked_var = tk.StringVar()
    sum_booked_var.set("Knjiženo: 0")
    sum_unbooked_var.set(f"Ostane: {len(df)}")
    ttk.Label(summary_info_frame, textvariable=sum_booked_var).grid(
        row=0, column=0, sticky="w", padx=(2, 0)
    )
    ttk.Label(summary_info_frame, textvariable=sum_unbooked_var).grid(
        row=1, column=0, sticky="w", padx=(2, 0)
    )

    # Desni del: drevo povzetka z drsnim trakom v svojem podoknu
    summary_right = tk.Frame(summary_frame)
    summary_right.pack(side="left", fill="both", expand=True)

    # Column keys and headers derive from :mod:`summary_columns`
    # to stay in sync with :data:`SUMMARY_COLS` used throughout the project.
    summary_cols = SUMMARY_KEYS
    summary_heads = SUMMARY_HEADS
    assert SUMMARY_COLS == summary_heads

    # Fiksno mapiranje med internimi ključi in prikazanimi naslovi
    key2head = dict(zip(summary_cols, summary_heads))

    summary_tree = ttk.Treeview(
        summary_right, columns=summary_cols, show="headings", height=5
    )
    vsb_summary = ttk.Scrollbar(
        summary_right, orient="vertical", command=summary_tree.yview
    )
    summary_tree.configure(yscrollcommand=vsb_summary.set)
    vsb_summary.pack(side="right", fill="y")
    summary_tree.pack(side="left", fill="both", expand=True)

    numeric_pairs = [
        ("kolicina_norm", "Količina"),
        ("vrednost", "Znesek"),
        ("rabata_pct", "Rabat (%)"),
        ("neto_po_rabatu", "Neto po rabatu"),
    ]
    numeric_cols = {k for k, _ in numeric_pairs} | {
        h for _, h in numeric_pairs
    }

    # glave in širine
    for c, h in zip(summary_cols, summary_heads):
        summary_tree.heading(c, text=h)
        summary_tree.column(
            c,
            width=120 if c in numeric_cols else 200,
            anchor="e" if c in numeric_cols else "w",
        )

    def _render_summary(df_summary: pd.DataFrame):
        """
        Vrstice v Treeview nariši robustno, ne glede na to ali je
        ``df_summary`` poimenovan z internimi ključi (``SUMMARY_KEYS``) ali
        z naslovi (``SUMMARY_HEADS``).
        """
        try:
            # 1) Odstrani podvojene stolpce v df (if any)
            # Če so v df_summary podvojene glave (npr. dva "WSM Naziv"),
            # izdamo opozorilo in obdržimo le prvo. _first_scalar poskrbi,
            # da ne izpišemo repr-ja Series kot večvrstičnega besedila.
            dup_cols = df_summary.columns[
                df_summary.columns.duplicated()
            ].tolist()
            if dup_cols:
                logging.getLogger(__name__).warning(
                    "SUMMARY duplicated columns: %s", dup_cols
                )
                df_summary = df_summary.loc[
                    :, ~df_summary.columns.duplicated()
                ].copy()
            cols_in_df = set(df_summary.columns.astype(str))

            # Po potrebi prerazporedi/ustvari stolpce v istem vrstnem redu
            # kot ``summary_cols`` (vrednosti za manjkajoče stolpce ostanejo
            # prazne)
            for iid in summary_tree.get_children():
                summary_tree.delete(iid)

            for i, row in df_summary.iterrows():
                values = []
                for key in summary_cols:
                    # Izberi pravi izvorni stolpec: najprej ključ, nato naslov
                    src_col = (
                        key if key in cols_in_df else key2head.get(key, key)
                    )
                    if src_col in cols_in_df:
                        v = _first_scalar(row[src_col])
                    else:
                        v = None

                    is_numeric = (key in numeric_cols) or (
                        src_col and src_col in numeric_cols
                    )
                    if is_numeric:
                        values.append(_fmt(v))
                    else:
                        if v is None:
                            values.append("")
                        else:
                            try:
                                txt = "" if pd.isna(v) else str(v)
                            except Exception:
                                txt = str(v) if v is not None else ""
                            # Treeview ne mara večvrstičnih vrednosti
                            if "\n" in txt or "\r" in txt:
                                txt = txt.replace("\r", " ").replace("\n", " ")
                            values.append(txt)

                summary_tree.insert("", "end", iid=str(i), values=values)
        except Exception as e:
            # Ne rušimo UI-ja zaradi renderja; samo zapišemo sled
            logging.getLogger(__name__).warning(
                "Render summary failed: %s (cols=%s)",
                e,
                list(df_summary.columns),
            )

    def _fallback_count_from_grid(df):
        try:
            codes = first_existing_series(
                df,
                ["_summary_key", "_booked_sifra", "WSM šifra", "wsm_sifra"],
            )
            if codes is None:
                codes = pd.Series([""] * len(df), index=df.index)
            codes = codes.astype("string").map(_norm_wsm_code)
            codes = codes.fillna("").str.upper()
            excluded = _excluded_codes_upper()
            booked_mask = codes.ne("") & ~codes.isin(excluded)

            if "status" in df.columns:
                st = (
                    df["status"]
                    .fillna("")
                    .astype(str)
                    .str.upper()
                    .str.strip()
                )
                status_mask = st.str.startswith(("POVEZANO", "AUTO"))
                booked_mask = booked_mask | (
                    status_mask & codes.ne("OSTALO") & codes.ne("")
                )

            booked = int(booked_mask.sum())
            remaining = int(len(df) - booked)
            return booked, remaining
        except Exception:
            return 0, len(df)

    def _update_summary():
        icon_holder = net_icon_label_holder

        df = globals().get("_CURRENT_GRID_DF")
        if df is None:
            df = globals().get("df")

        if df is not None:
            df = df.loc[:, ~df.columns.duplicated()].copy()

        if df is None or df.empty:
            _render_summary(summary_df_from_records([]))
            globals()["_SUMMARY_COUNTS"] = (0, 0)
            try:
                sum_booked_var.set("Knjiženo: 0")
                sum_unbooked_var.set("Ostane: 0")
            except Exception:
                pass
            net_icon_label = icon_holder["widget"]
            if net_icon_label is not None and getattr(
                net_icon_label, "winfo_exists", lambda: False
            )():
                net_icon_label.config(text="")
            return

        header_net_for_summary = globals().get("header_net_dec")
        if header_net_for_summary is None:
            try:
                header_net_for_summary = (
                    header_totals.get("net")
                    if isinstance(header_totals.get("net"), Decimal)
                    else Decimal(str(header_totals.get("net")))
                )
            except Exception:
                header_net_for_summary = None

        # --- novi povzetek z OSTALO vrstico ---
        summary_df, summary_status, net_diff_val = _build_wsm_summary(
            df, header_net_for_summary
        )

        _render_summary(summary_df)

        # --- števec "Knjiženo / Ostane" po vrsticah v gridu ---
        codes_series = (
            df.get("_booked_sifra")
            if "_booked_sifra" in df.columns
            else df.get("wsm_sifra")
        )
        if codes_series is None:
            booked_count = 0
            remaining_count = len(df)
        else:
            codes_series = codes_series.astype("string").fillna("").str.strip()
            booked_count = int((codes_series != "").sum())
            remaining_count = int(len(df) - booked_count)

        globals()["_SUMMARY_COUNTS"] = (booked_count, remaining_count)
        try:
            sum_booked_var.set(f"Knjiženo: {booked_count}")
            sum_unbooked_var.set(f"Ostane: {remaining_count}")
        except Exception:
            pass

        if "ttk" not in globals():
            return

        # --- NET indikator (X / Δ) ---
        net_icon_label = icon_holder["widget"]
        if net_icon_label is None or not net_icon_label.winfo_exists():
            net_icon_label = ttk.Label(total_frame)
            net_icon_label.pack(side="left", padx=5)

        if summary_status == "X":
            net_icon_label.config(text="✗", style="Indicator.Red.TLabel")
            tooltip = (
                f"Razlika v neto znesku je {net_diff_val:+.2f} € (preveri račun!)."
                if net_diff_val is not None
                else "Razlika v neto znesku – preveri račun!"
            )
        elif summary_status == "Δ":
            net_icon_label.config(text="△", style="TLabel")
            tooltip = (
                f"Razlika v neto znesku je {net_diff_val:+.2f} € (verjetno zaokroževanje)."
                if net_diff_val is not None
                else "Razlika v neto znesku (verjetno zaokroževanje)."
            )
        else:
            net_icon_label.config(text="", style="TLabel")
            tooltip = None

        _bind_status_tooltip(net_icon_label, tooltip)
        icon_holder["widget"] = net_icon_label

    def format_eur(value: Decimal | float | int | str) -> str:
        try:
            dec_val = value if isinstance(value, Decimal) else Decimal(str(value))
        except Exception:
            dec_val = Decimal("0")
        dec_val = dec_val.quantize(Decimal("0.01"))
        formatted = f"{dec_val:,.2f}".replace(",", " ")
        formatted = formatted.replace(".", ",").replace(" ", ".")
        return f"{formatted} €"

    # Skupni zneski pod povzetkom
    total_frame = tk.Frame(root)
    total_frame.pack(fill="x", pady=5)

    if df is not None and "ddv" in df.columns:
        vat_series = df["ddv"].apply(lambda x: _as_dec(x, "0"))
    else:
        vat_series = pd.Series(
            [_as_dec("0", "0")] * (len(df) if df is not None else 0),
            index=(df.index if df is not None else None),
        )
    vat_total = _sum_decimal(vat_series).quantize(Decimal("0.01"))
    inv_total = (
        header_totals["gross"]
        if isinstance(header_totals["gross"], Decimal)
        else Decimal(str(header_totals["gross"]))
    )
    inv_total = inv_total.quantize(Decimal("0.01"))
    calc_total = net_total + vat_total
    summary_totals.update({"net": net_total, "vat": vat_total, "gross": calc_total})
    tolerance = _resolve_tolerance(net_total, inv_total)
    diff = inv_total - calc_total
    net_status = classify_net_difference(
        header_net_dec, net_total, tolerance=tolerance
    )
    net_diff = (
        (net_total - header_net_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if header_net_dec is not None
        else None
    )
    if abs(diff) > tolerance:
        if doc_discount:
            diff2 = inv_total - (calc_total + abs(doc_discount))
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
    gross = calc_total

    lbl_net = tk.Label(
        total_frame,
        text=f"Neto: {format_eur(net)}",
        font=("Arial", 10, "bold"),
        name="total_net",
    )
    lbl_net.pack(side="left", padx=10)
    lbl_vat = tk.Label(
        total_frame,
        text=f"DDV: {format_eur(vat)}",
        font=("Arial", 10, "bold"),
        name="total_vat",
    )
    lbl_vat.pack(side="left", padx=10)
    lbl_gross = tk.Label(
        total_frame,
        text=f"Skupaj: {format_eur(gross)}",
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

    def _hide_status_tip(_=None):
        nonlocal status_tip
        if status_tip is not None:
            try:
                status_tip.destroy()
            except Exception:
                pass
            status_tip = None

    def _show_status_tip(widget: tk.Widget, text: str | None) -> None:
        nonlocal status_tip
        _hide_status_tip()
        if not text:
            return
        try:
            status_tip = tk.Toplevel(root)
            status_tip.wm_overrideredirect(True)
            tk.Label(
                status_tip,
                text=text,
                background="#ffe6b3",
                relief="solid",
                borderwidth=1,
                wraplength=320,
            ).pack()
            status_tip.geometry(
                f"+{widget.winfo_rootx()}+{widget.winfo_rooty()+widget.winfo_height()}"
            )

            try:
                widget.focus_set()
            except Exception:
                pass
        except Exception as exc:
            log.debug("Prikaz tooltipa ni uspel: %s", exc)
            _hide_status_tip()

    def _bind_status_tooltip(widget: tk.Widget, text: str | None) -> None:
        if widget is None:
            return
        widget.bind(
            "<Enter>",
            lambda _e, w=widget, t=text: _show_status_tip(w, t),
        )
        widget.bind("<Leave>", _hide_status_tip)

    _bind_status_tooltip(
        lbl_net,
        (
            "Seštevek neto zneskov vseh postavk po upoštevanih popustih "
            "(enako kot neto znesek na računu)."
        ),
    )

    if net_status == "rounding":
        net_icon = ttk.Label(
            total_frame,
            text="△",
            foreground="#d48c00",
        )
        net_icon.pack(side="left", padx=5)
        diff_text = (
            f"{net_diff:+.2f} €" if isinstance(net_diff, Decimal) else None
        )
        tooltip = (
            f"Razlika v neto znesku je {diff_text} (verjetno zaokroževanje)."
            if diff_text
            else "Razlika v neto znesku (verjetno zaokroževanje)."
        )
        _bind_status_tooltip(net_icon, tooltip)
        net_icon_label_holder["widget"] = net_icon
    elif net_status == "mismatch":
        net_icon = ttk.Label(
            total_frame, text="✗", style="Indicator.Red.TLabel"
        )
        net_icon.pack(side="left", padx=5)
        diff_text = (
            f"{net_diff:+.2f} €" if isinstance(net_diff, Decimal) else None
        )
        tooltip = (
            f"Razlika v neto znesku je {diff_text} (preveri račun!)."
            if diff_text
            else "Razlika v neto znesku – preveri račun!"
        )
        _bind_status_tooltip(net_icon, tooltip)
        net_icon_label_holder["widget"] = net_icon

    indicator_label = ttk.Label(
        total_frame, text="", style="Indicator.Red.TLabel"
    )
    indicator_label.pack(side="left", padx=5)
    _status_var = tk.StringVar(value="")
    status_count_label = ttk.Label(total_frame, textvariable=_status_var)
    status_count_label.pack(side="left", padx=5)

    # --- Legenda za ikone neto stanja ---
    legend_frame = tk.Frame(total_frame)
    legend_frame.pack(side="right", padx=10)

    legend_label_error = tk.Label(
        legend_frame,
        text="✗ – razlika v neto znesku, samodejno ujemanje onemogočeno",
        font=("Arial", 8),
        anchor="w",
    )
    legend_label_error.pack(anchor="w")

    legend_label_warn = tk.Label(
        legend_frame,
        text="△ – razlika v neto znesku (verjetno zaokroževanje)",
        font=("Arial", 8),
        anchor="w",
    )
    legend_label_warn.pack(anchor="w")

    legend_label_net = tk.Label(
        legend_frame,
        text=(
            "Neto – seštevek neto zneskov vseh postavk po upoštevanih popustih "
            "(enako kot neto znesek na računu)"
        ),
        font=("Arial", 8),
        anchor="w",
    )
    legend_label_net.pack(anchor="w")

    def _safe_update_totals():
        nonlocal summary_totals
        if closing or not root.winfo_exists():
            return

        warn_state = getattr(_safe_update_totals, "_warn_state", {"val": None})
        _safe_update_totals._warn_state = warn_state

        try:
            _format_eur = format_eur  # type: ignore[name-defined]
        except Exception:
            def _format_eur(value: Decimal | float | int | str) -> str:
                try:
                    dec_val = (
                        value if isinstance(value, Decimal) else Decimal(str(value))
                    )
                except Exception:
                    dec_val = Decimal("0")
                dec_val = dec_val.quantize(Decimal("0.01"))
                formatted = f"{dec_val:,.2f}".replace(",", " ")
                formatted = formatted.replace(".", ",").replace(" ", ".")
                return f"{formatted} €"

        df_cur = globals().get("_CURRENT_GRID_DF")
        if df_cur is None:
            df_cur = df
        if df_cur is not None:
            df_cur = df_cur.loc[:, ~df_cur.columns.duplicated()].copy()

        if df_cur is not None and "total_net" in df_cur.columns:
            net_series = df_cur["total_net"].apply(lambda x: _as_dec(x, "0"))
        elif df_cur is not None and "vrednost" in df_cur.columns:
            net_series = df_cur["vrednost"].apply(lambda x: _as_dec(x, "0"))
        else:
            net_series = pd.Series(
                [_as_dec("0", "0")] * (len(df_cur) if df_cur is not None else 0),
                index=(df_cur.index if df_cur is not None else None),
            )
        if df_cur is not None and "ddv" in df_cur.columns:
            ddv_series = df_cur["ddv"].apply(lambda x: _as_dec(x, "0"))
        else:
            ddv_series = pd.Series(
                [_as_dec("0", "0")] * (len(df_cur) if df_cur is not None else 0),
                index=(df_cur.index if df_cur is not None else None),
            )
        doc_disc = _as_dec(doc_discount, "0").quantize(Decimal("0.01"))
        net_total = (_sum_decimal(net_series) + doc_disc).quantize(Decimal("0.01"))
        vat_val = _sum_decimal(ddv_series).quantize(Decimal("0.01"))
        calc_total = (net_total + vat_val).quantize(Decimal("0.01"))
        summary_totals.update({"net": net_total, "vat": vat_val, "gross": calc_total})
        inv_total = (
            header_totals["gross"]
            if isinstance(header_totals["gross"], Decimal)
            else Decimal(str(header_totals["gross"]))
        )
        inv_total = inv_total.quantize(Decimal("0.01"))
        tolerance = _resolve_tolerance(net_total, inv_total)
        diff = inv_total - calc_total
        difference = abs(diff)
        if difference > tolerance:
            msg = (
                "Razlika med postavkami in računom je "
                f"{diff:+.2f} € in presega dovoljeno zaokroževanje."
            )
            if warn_state["val"] != msg:
                warn_state["val"] = msg
                messagebox.showwarning("Opozorilo", msg)
        else:
            # razlika je OK -> dovoli prihodnja opozorila
            warn_state["val"] = None

        net = net_total
        vat = vat_val
        gross = calc_total
        try:
            _classify_net_difference = classify_net_difference
        except Exception:
            _classify_net_difference = globals().get("classify_net_difference")
        if not callable(_classify_net_difference):
            _classify_net_difference = lambda *_args, **_kwargs: "ok"
        try:
            _round_half_up = ROUND_HALF_UP
        except Exception:
            _round_half_up = getattr(Decimal, "ROUND_HALF_UP", None) or "ROUND_HALF_UP"
        try:
            header_net_dec = (
                header_totals.get("net")
                if isinstance(header_totals.get("net"), Decimal)
                else Decimal(str(header_totals.get("net")))
            )
        except Exception:
            header_net_dec = None
        net_for_header_compare = net_total
        net_status = _classify_net_difference(
            header_net_dec, net_for_header_compare, tolerance=tolerance
        )

        net_diff = (
            (header_net_dec - net_for_header_compare).quantize(
                Decimal("0.01"), rounding=_round_half_up
            )
            if header_net_dec is not None
            else None
        )

        explained_by_doc_discount = False
        default_net_icon = net_icon_label_holder["widget"]
        net_icon_label_ref = getattr(
            _safe_update_totals, "_net_icon", default_net_icon
        )
        if net_status == "ok" or explained_by_doc_discount:
            if net_icon_label_ref and getattr(net_icon_label_ref, "winfo_exists", lambda: False)():
                try:
                    net_icon_label_ref.pack_forget()
                    net_icon_label_ref.destroy()
                except Exception:
                    pass
                net_icon_label_ref = None
        else:
            if net_icon_label_ref is None or not net_icon_label_ref.winfo_exists():
                net_icon_label_ref = ttk.Label(total_frame)
                net_icon_label_ref.pack(side="left", padx=5)
            try:
                _hide_status_tip()
            except Exception:
                pass
            if net_status == "rounding":
                diff_text = f"{net_diff:+.2f} €" if net_diff is not None else None
                tooltip = (
                    f"Razlika v neto znesku je {diff_text} (verjetno zaokroževanje)."
                    if diff_text
                    else "Razlika v neto znesku (verjetno zaokroževanje)."
                )
                net_icon_label_ref.config(text="△", style="TLabel")
                try:
                    net_icon_label_ref.configure(foreground="#d48c00")
                except Exception:
                    pass
            else:
                diff_text = f"{net_diff:+.2f} €" if net_diff is not None else None
                tooltip = (
                    f"Razlika v neto znesku je {diff_text} (preveri račun!)."
                    if diff_text
                    else "Razlika v neto znesku – preveri račun!"
                )
                net_icon_label_ref.config(text="✗", style="Indicator.Red.TLabel")
                try:
                    net_icon_label_ref.configure(foreground="")
                except Exception:
                    pass
            _bind_status_tooltip(net_icon_label_ref, tooltip)
            try:
                net_icon_label_ref.pack_configure(padx=5)
            except Exception:
                pass
        _safe_update_totals._net_icon = net_icon_label_ref
        net_icon_label_holder["widget"] = net_icon_label_ref
        try:
            eslog_mode = eslog_totals.mode  # type: ignore[name-defined]
        except Exception:
            eslog_mode = None

        header_ok = (
            eslog_mode != "error" if eslog_mode is not None else difference <= tolerance
        )
        try:
            if indicator_label is None or not indicator_label.winfo_exists():
                return
            indicator_label.config(
                text="✓" if header_ok else "✗",
                style=(
                    "Indicator.Green.TLabel"
                    if header_ok
                    else "Indicator.Red.TLabel"
                ),
            )
        except tk.TclError:
            return
        header_net_disp = _as_dec(header_totals.get("net"), net)
        header_vat_disp = _as_dec(header_totals.get("vat"), vat)
        header_gross_disp = _as_dec(header_totals.get("gross"), gross)

        widget = total_frame.children.get("total_net")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"Neto: {_format_eur(header_net_disp)}")
        widget = total_frame.children.get("total_vat")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"DDV: {_format_eur(header_vat_disp)}")
        widget = total_frame.children.get("total_gross")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(text=f"Skupaj: {_format_eur(header_gross_disp)}")
        widget = total_frame.children.get("total_sum")
        if widget and getattr(widget, "winfo_exists", lambda: True)():
            widget.config(
                text=(
                    f"Neto:   {_format_eur(header_net_disp)}\n"
                    f"DDV:    {_format_eur(header_vat_disp)}\n"
                    f"Skupaj: {_format_eur(header_gross_disp)}"
                )
            )

    _safe_update_totals._warn_state = {"val": None}

    def _schedule_totals():
        nonlocal _after_totals_id
        if closing or not root.winfo_exists():
            return
        _after_totals_id = root.after(250, _safe_update_totals)
        try:
            b, u = globals().get("_SUMMARY_COUNTS", (None, None))
            if b is None:
                raise KeyError
        except Exception:
            b, u = _fallback_count_from_grid(df)
        _status_var.set(f"Knjiženo: {b} | Ostane: {u}")

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
    lb = tk.Listbox(entry_frame, height=10)
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

    def _apply_saved_links_now(_silent: bool = False):
        nonlocal df
        links_df = globals().get("_PENDING_LINKS_DF")
        if links_df is None or getattr(links_df, "empty", True):
            if not _silent:
                messagebox.showinfo(
                    "Povezave", "Ni shranjenih povezav za uveljavitev."
                )
            return
        try:
            df, upd_cnt = _apply_links_to_df(df, links_df)
            _normalize_override_column()
            _recalculate_units()
            df = _fill_names_from_catalog(df, wsm_df)
            df = _normalize_wsm_display_columns(df)
            # osveži vidne celice v gridu (Treeview)
            try:
                for idx in df.index:
                    rid = str(idx)
                    if (
                        "WSM šifra" in df.columns
                        and tree.exists(rid)
                        and _tree_has_col("WSM šifra")
                    ):
                        val = _first_scalar(df.at[idx, "WSM šifra"])
                        tree.set(
                            rid,
                            "WSM šifra",
                            "" if val is None or pd.isna(val) else val,
                        )
                    # od tu naprej je ime enotno v df: "WSM Naziv"
                    if tree.exists(rid):
                        for col_alias in ("WSM Naziv", "WSM naziv"):
                            if _tree_has_col(col_alias):
                                v = (
                                    _first_scalar(df.at[idx, "WSM Naziv"])
                                    if "WSM Naziv" in df.columns
                                    else None
                                )
                                s = "" if v is None or pd.isna(v) else str(v)
                                tree.set(rid, col_alias, s)
                                break

                    if (
                        "rabat_opis" in df.columns
                        and tree.exists(rid)
                        and _tree_has_col("rabat_opis")
                    ):
                        v = _first_scalar(df.at[idx, "rabat_opis"])
                        tree.set(
                            rid,
                            "rabat_opis",
                            "" if v is None or pd.isna(v) else str(v),
                        )

                    if (
                        "status" in df.columns
                        and tree.exists(rid)
                        and _tree_has_col("status")
                    ):
                        v = _first_scalar(df.at[idx, "status"])
                        tree.set(
                            rid,
                            "status",
                            "" if v is None or pd.isna(v) else str(v),
                        )
                        if (
                            "rabata_pct" in df.columns
                            and tree.exists(rid)
                            and _tree_has_col("rabata_pct")
                        ):
                            tree.set(
                                rid,
                                "rabata_pct",
                                _fmt(df.at[idx, "rabata_pct"]),
                            )
                    if (
                        "kolicina_norm" in df.columns
                        and tree.exists(rid)
                        and _tree_has_col("kolicina_norm")
                    ):
                        tree.set(
                            rid,
                            "kolicina_norm",
                            _fmt(df.at[idx, "kolicina_norm"]),
                        )
                    if (
                        "enota_norm" in df.columns
                        and tree.exists(rid)
                        and _tree_has_col("enota_norm")
                    ):
                        unit_val = df.at[idx, "enota_norm"]
                        try:
                            unit_disp = "" if pd.isna(unit_val) else str(unit_val)
                        except Exception:
                            unit_disp = str(unit_val)
                        tree.set(rid, "enota_norm", unit_disp)
            except Exception as e:
                log.warning("Osvežitev grid celic ni uspela: %s", e)
            _apply_saved_multipliers(
                df,
                links_df,
                tree=tree,
                update_summary=_update_summary,
                update_totals=_schedule_totals,
            )
            # posodobi referenco na aktualni df pred povzetkom
            globals()["_CURRENT_GRID_DF"] = df
            _update_summary()
            _schedule_totals()
            if not _silent:
                messagebox.showinfo(
                    "Povezave", f"Uveljavljenih povezav: {upd_cnt}"
                )
        except Exception as e:
            log.exception("Ročna uveljavitev povezav ni uspela: %s", e)
            if not _silent:
                messagebox.showerror(
                    "Povezave", f"Napaka pri uveljavitvi povezav:\n{e}"
                )

    # --- Unit change widgets ---
    unit_options = ["kos", "kg", "L"]

    # Če smo povezave auto-uveljavili že ob odpiranju, zdaj osveži še grid.
    if auto_apply_links:
        try:
            root.after(0, lambda: _apply_saved_links_now(_silent=True))
        except Exception as e:
            log.debug("AUTO refresh WSM stolpcev v gridu preskočen: %s", e)

    def _cleanup():
        nonlocal closing, price_tip, last_warn_item, status_tip
        closing = True
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
        if status_tip is not None:
            try:
                status_tip.destroy()
            except Exception:
                pass
            status_tip = None

    def _finalize_and_save(_=None):
        _update_summary()
        _safe_update_totals()
        _cleanup()
        df["dobavitelj"] = supplier_name
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
    try:
        btn_apply_links = ttk.Button(
            btn_frame,
            text="Uporabi shranjene povezave",
            command=_apply_saved_links_now,
        )
        btn_apply_links.grid(row=0, column=2, padx=(6, 0))
    except Exception:
        pass
    save_btn.grid(row=0, column=0, padx=(6, 0))
    exit_btn.grid(row=0, column=1, padx=(6, 0))

    root.bind("<F10>", _finalize_and_save)
    bindings.append((root, "<F10>"))

    root.bind("<Control-l>", lambda _e: _apply_saved_links_now())
    bindings.append((root, "<Control-l>"))

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
        _suggest_on_focus["val"] = ENABLE_WSM_SUGGESTIONS
        entry.focus_set()
        if ENABLE_WSM_SUGGESTIONS:
            try:
                _open_suggestions_if_needed()
            except Exception:
                pass
        return "break"

    def _open_suggestions_if_needed():
        """Open the suggestion dropdown if it's not already visible."""
        if not ENABLE_WSM_SUGGESTIONS:
            _close_suggestions(entry, lb)
            return
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
        if not ENABLE_WSM_SUGGESTIONS:
            _close_suggestions(entry, lb)
            return
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
        if not ENABLE_WSM_SUGGESTIONS:
            entry.focus_set()
            _close_suggestions(entry, lb)
            return "break"
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
        if ENABLE_WSM_SUGGESTIONS and _suggest_on_focus["val"]:
            _open_suggestions_if_needed()

    def _start_editing_from_tree(_evt=None):
        """Enter na tabeli začne vnos (focus v Entry + predlogi)."""
        try:
            entry.focus_set()
            if ENABLE_WSM_SUGGESTIONS:
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
        sel_i = tree.focus()
        if sel_i:
            try:
                idx = int(sel_i)
            except Exception:
                idx = None
            if idx is not None and _row_has_booked_code(idx):
                entry.delete(0, "end")
                _clear_wsm_connection()
                return "break"
        entry.delete(0, "end")
        tree.focus_set()
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

        current_override = None
        if "override_unit" in df.columns:
            try:
                override_val = df.at[idx, "override_unit"]
                if not pd.isna(override_val) and str(override_val).strip():
                    current_override = str(override_val).strip()
            except Exception:
                current_override = None

        initial = current_override or str(df.at[idx, "enota_norm"] or "")
        if initial not in unit_options:
            initial = unit_options[0]

        top = tk.Toplevel(root)
        top.title("Spremeni enoto")
        var = tk.StringVar(value=initial)
        cb = ttk.Combobox(
            top, values=unit_options, textvariable=var, state="readonly"
        )
        cb.pack(padx=10, pady=10)
        log.debug("Edit dialog opened with value %s", var.get())

        def _apply_override(selected: str | None) -> None:
            override_val = selected.strip() if selected else ""
            if "override_unit" not in df.columns:
                df["override_unit"] = pd.Series(pd.NA, index=df.index, dtype="string")
            df.at[idx, "override_unit"] = (
                override_val if override_val else pd.NA
            )
            _normalize_override_column()

            raw_qty = df.at[idx, "kolicina"] if "kolicina" in df.columns else Decimal("0")
            qty_dec = raw_qty if isinstance(raw_qty, Decimal) else _as_dec(raw_qty, "0")
            raw_unit = df.at[idx, "enota"] if "enota" in df.columns else ""
            name_val = df.at[idx, "naziv"] if "naziv" in df.columns else ""
            vat_val = df.at[idx, "ddv_stopnja"] if "ddv_stopnja" in df.columns else None
            code_val = df.at[idx, "sifra_artikla"] if "sifra_artikla" in df.columns else None
            override_for_calc = override_val if override_val else None
            qty_norm, unit_norm = _norm_unit(
                qty_dec,
                raw_unit,
                name_val,
                vat_val,
                code_val,
                override_unit=override_for_calc,
            )
            df.at[idx, "kolicina_norm"] = qty_norm
            df.at[idx, "enota_norm"] = unit_norm

            if tree.exists(row_id) and _tree_has_col("kolicina_norm"):
                tree.set(row_id, "kolicina_norm", _fmt(qty_norm))
            if tree.exists(row_id) and _tree_has_col("enota_norm"):
                tree.set(row_id, "enota_norm", unit_norm)

            new_vals = [_safe_cell(idx, c) for c in cols]
            tree.item(row_id, values=new_vals)

            log.info(
                "Updated row %s override=%s -> unit=%s quantity=%s",
                idx,
                override_val if override_val else "AUTO",
                unit_norm,
                qty_norm,
            )

            _update_summary()
            _schedule_totals()

        def _apply(_=None):
            new_u = var.get()
            _apply_override(new_u)
            top.destroy()

        def _reset_to_auto():
            _apply_override(None)
            top.destroy()

        btn_frame = tk.Frame(top)
        btn_frame.pack(pady=(0, 10))
        tk.Button(btn_frame, text="OK", command=_apply).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Samodejno", command=_reset_to_auto).pack(
            side="left", padx=5
        )

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

    def _safe_cell(idx, c, default=""):
        """Safely extract display values for ``tree`` columns.

        The grid's DataFrame ``df`` can change shape after merges or edits,
        therefore individual columns might temporarily disappear.  Accessing a
        missing column would raise, so this helper mirrors the previous
        inline logic and makes it available to all callbacks that need to
        refresh the visible row values.
        """

        if c not in df.columns:
            return default
        try:
            v = df.at[idx, c]
        except Exception:
            return default
        if isinstance(v, (Decimal, float, int)) and not isinstance(v, bool):
            return _fmt(_clean_neg_zero(v))
        if v is None or (hasattr(pd, "isna") and pd.isna(v)):
            return ""
        return str(v)

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
        # 1) Ugotovi kodo tudi, če je uporabnik vpisal kodo (ne naziv)
        code = n2s.get(choice, pd.NA)
        if (pd.isna(code) or str(code).strip() == "") and str(
            choice
        ).strip() != "":
            try:
                s_codes = set(wsm_df["wsm_sifra"].astype(str))
            except Exception:
                s_codes = set()
            if str(choice).strip() in s_codes:
                code = str(choice).strip()

        # 2) Pridobi pravi naziv iz kataloga glede na kodo
        name_from_catalog = None
        if not pd.isna(code):
            try:
                _map = (
                    wsm_df.assign(wsm_sifra=wsm_df["wsm_sifra"].astype(str))
                    .dropna(subset=["wsm_naziv"])
                    .drop_duplicates("wsm_sifra")
                    .set_index("wsm_sifra")["wsm_naziv"]
                )
                name_from_catalog = _map.get(str(code))
            except Exception:
                name_from_catalog = None

        # 3) Odloči končni naziv:
        #    - če imamo naziv iz kataloga, uporabi njega
        #    - sicer, če je uporabniški 'choice' prazen ali 'ostalo',
        #      uporabi kar kodo
        #    - v nasprotnem primeru pusti 'choice'
        if name_from_catalog and str(name_from_catalog).strip():
            name = str(name_from_catalog)
        else:
            if (
                str(choice).strip() == ""
                or str(choice).strip().lower() == "ostalo"
            ):
                name = "" if pd.isna(code) else str(code)
            else:
                name = str(choice).strip()

        # VARNOSTNI PAS: nikoli ne pusti 'ostalo' pri knjiženih
        if name.strip().lower() == "ostalo" and not pd.isna(code):
            name = str(code)

        # Zapiši v DataFrame (interno in display kopije)
        df.at[idx, "wsm_sifra"] = pd.NA if pd.isna(code) else str(code)
        df.at[idx, "wsm_naziv"] = pd.NA if name == "" else str(name)
        df.at[idx, "status"] = "POVEZANO"
        if "WSM šifra" in df.columns:
            df.at[idx, "WSM šifra"] = "" if pd.isna(code) else str(code)
        # posodobi oba možna stolpca imena, če obstajata
        for name_col in ("WSM naziv", "WSM Naziv"):
            if name_col in df.columns:
                df.at[idx, name_col] = "" if name == "" else str(name)
        try:
            tree.set(sel_i, "WSM šifra", "" if pd.isna(code) else str(code))
            tree_cols = set(tree["columns"])
            if "WSM naziv" in tree_cols:
                tree.set(sel_i, "WSM naziv", "" if name == "" else str(name))
            elif "WSM Naziv" in tree_cols:
                tree.set(sel_i, "WSM Naziv", "" if name == "" else str(name))
        except Exception:
            pass
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

        booked_value = _coerce_booked_code(
            None if pd.isna(code) else str(code)
        )
        if "_booked_sifra" in df.columns:
            df.at[idx, "_booked_sifra"] = booked_value
        if "_summary_key" in df.columns:
            df.at[idx, "_summary_key"] = booked_value

        _show_tooltip(sel_i, tooltip)
        if "is_gratis" in df.columns and df.at[idx, "is_gratis"]:
            tset = set(tree.item(sel_i).get("tags", ()))
            tset.add("gratis")
            tree.item(sel_i, tags=tuple(tset))
            tree.set(sel_i, "warning", "GRATIS")

        new_vals = [_safe_cell(idx, c) for c in cols]
        for j, c in enumerate(cols):
            if _tree_has_col(c):
                tree.set(sel_i, c, new_vals[j])
        try:
            globals()["_CURRENT_GRID_DF"] = df
            _update_summary()
            # posodobi tudi skupne seštevke po potrditvi
            _schedule_totals()
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

    def _row_has_booked_code(idx: int) -> bool:
        try:
            if "_booked_sifra" in df.columns:
                booked_val = df.at[idx, "_booked_sifra"]
                if not pd.isna(booked_val):
                    text = str(booked_val).strip()
                    if text and text.upper() != "OSTALO":
                        return True
        except Exception:
            pass
        try:
            if "wsm_sifra" in df.columns:
                code_val = df.at[idx, "wsm_sifra"]
                if not pd.isna(code_val):
                    text = str(code_val).strip()
                    if text and text.upper() != "OSTALO":
                        return True
        except Exception:
            pass
        return False

    def _clear_wsm_connection(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = pd.NA
        df.at[idx, "wsm_sifra"] = pd.NA
        df.at[idx, "status"] = pd.NA
        if "WSM šifra" in df.columns:
            df.at[idx, "WSM šifra"] = ""
        for display_col in ("WSM Naziv", "WSM naziv"):
            if display_col in df.columns:
                df.at[idx, display_col] = ""
        cleared_value = _coerce_booked_code(None)
        if "_booked_sifra" in df.columns:
            df.at[idx, "_booked_sifra"] = cleared_value
        if "_summary_key" in df.columns:
            df.at[idx, "_summary_key"] = cleared_value
        try:
            tree_cols = set(tree["columns"])
        except Exception:
            tree_cols = set()
        try:
            if "WSM šifra" in tree_cols:
                tree.set(sel_i, "WSM šifra", "")
            for name_col in ("WSM naziv", "WSM Naziv"):
                if name_col in tree_cols:
                    tree.set(sel_i, name_col, "")
        except Exception:
            pass
        new_vals = [_safe_cell(idx, c) for c in cols]
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
    multiplier_btn.grid(row=0, column=3, padx=(6, 0))

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

    # poravnaj prikazne stolpce z internimi
    if "wsm_sifra" in df.columns and "WSM šifra" in df.columns:
        df["WSM šifra"] = df["wsm_sifra"]
    if "wsm_naziv" in df.columns and "WSM Naziv" in df.columns:
        df["WSM Naziv"] = df["wsm_naziv"]

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

    # deduplikacija stolpcev in poravnava z df_doc,
    # da se concat ne zalomi in da je vrstni red stabilen
    df = df.loc[:, ~df.columns.duplicated()].copy()
    all_cols = list(
        dict.fromkeys(
            list(df.columns)
            + [c for c in df_doc.columns if c not in df.columns]
        )
    )
    df = df.reindex(columns=all_cols)
    df_doc = df_doc.reindex(columns=all_cols)

    return pd.concat([df, df_doc], ignore_index=True)
