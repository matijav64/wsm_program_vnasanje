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

# Naj se shranjene povezave uporabijo samodejno ob odprtju?
# (privzeto NE)
AUTO_APPLY_LINKS = os.getenv(
    "AUTO_APPLY_LINKS", os.getenv("WSM_AUTO_APPLY_LINKS", "0")
) not in {
    "0",
    "false",
    "False",
}

# Ali naj pri knjiženih vrsticah prepišemo tudi 'Ostalo' z nazivom iz kataloga?
OVERWRITE_OSTALO_IN_GRID = os.getenv(
    "WSM_OVERWRITE_OSTALO_IN_GRID", "1"
) not in {"0", "false", "False"}

DEC2 = Decimal("0.01")
DEC_PCT_MIN = Decimal("-100")
DEC_PCT_MAX = Decimal("100")

EXCLUDED_CODES = {"UNKNOWN", "OSTALO", "OTHER", "NAN"}


def _excluded_codes_upper() -> frozenset[str]:
    """Return ``EXCLUDED_CODES`` uppercased.

    Evaluated on each call so tests/plugins may adjust ``EXCLUDED_CODES`` at
    runtime without stale cached values.
    """
    return frozenset(x.upper() for x in EXCLUDED_CODES)


# Regex za prepoznavo "glavin" vrstic (Dobavnica/Račun/...).
# Možno razširiti z okoljsko spremenljivko ``WSM_HEADER_PREFIX``.
HDR_PREFIX_RE = re.compile(
    os.environ.get(
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
    df: pd.DataFrame, links_df: pd.DataFrame
) -> tuple[pd.DataFrame, int]:
    """Auto-apply stored links to ``df``.

    Najprej poskusi strogo ujemanje po ``sifra_dobavitelja`` + ``naziv_ckey`` +
    ``enota_norm``.  Za vrstice, kjer WSM koda še vedno manjka, se izvede
    fallback brez enote.  Funkcija vrne posodobljen ``df`` in število
    posodobljenih vrstic.
    """
    if df is None or df.empty or links_df is None or links_df.empty:
        return df, 0

    req = {"sifra_dobavitelja", "naziv_ckey"}
    if not req.issubset(df.columns) or not req.issubset(links_df.columns):
        return df, 0

    def _norm_unit(u):
        s = str(u or "").strip().lower()
        if s in {"kom", "kosov", "kos/kos"}:
            return "kos"
        return s

    excluded = _excluded_codes_upper()

    def _needs_fill(s: pd.Series) -> pd.Series:
        s = s.astype("string")
        s_str = s.str.strip()
        return (
            s.isna()
            | s_str.eq("")
            | s_str.eq("<NA>")
            | s_str.str.upper().isin(excluded)
        )

    # normalizacija enot
    links_df = links_df.copy()
    links_df["enota_norm"] = (
        links_df["enota_norm"].map(_norm_unit)
        if "enota_norm" in links_df.columns
        else pd.Series([""] * len(links_df), index=links_df.index)
    )
    df["enota_norm"] = (
        df["enota_norm"].map(_norm_unit)
        if "enota_norm" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )

    for c in ["sifra_dobavitelja", "naziv_ckey"]:
        df[c] = df[c].astype(str)
        links_df[c] = links_df[c].astype(str)

    if "wsm_sifra" not in links_df.columns:
        return df, 0

    name_col = (
        "wsm_naziv"
        if "wsm_naziv" in links_df.columns
        else ("WSM Naziv" if "WSM Naziv" in links_df.columns else None)
    )

    links_df["wsm_sifra"] = links_df["wsm_sifra"].astype(str).str.strip()
    links_df = links_df[links_df["wsm_sifra"] != ""]

    if "wsm_sifra" not in df.columns:
        df["wsm_sifra"] = pd.Series(pd.NA, index=df.index, dtype="string")
    if "wsm_naziv" not in df.columns:
        df["wsm_naziv"] = pd.Series(pd.NA, index=df.index, dtype="string")

    mask_initial = _needs_fill(df.get("wsm_sifra"))
    before = int((~mask_initial).sum())

    # 1) strogo ujemanje: dobavitelj + naziv_ckey + enota_norm
    key_strict = ["sifra_dobavitelja", "naziv_ckey", "enota_norm"]
    if mask_initial.any() and all(k in links_df.columns for k in key_strict):
        lk1_cols = (
            key_strict + ["wsm_sifra"] + ([name_col] if name_col else [])
        )
        lk1 = (
            links_df[lk1_cols]
            .dropna(subset=["wsm_sifra"])
            .drop_duplicates(key_strict)
        )
        m1 = df.loc[mask_initial, key_strict].merge(
            lk1, on=key_strict, how="left"
        )
        idx1 = df.index[mask_initial]
        codes1 = pd.Series(m1["wsm_sifra"].values, index=idx1)
        df.loc[idx1, "wsm_sifra"] = df.loc[idx1, "wsm_sifra"].where(
            ~_needs_fill(df.loc[idx1, "wsm_sifra"]), codes1
        )
        if name_col:
            names1 = pd.Series(m1[name_col].values, index=idx1)
            df.loc[idx1, "wsm_naziv"] = df.loc[idx1, "wsm_naziv"].where(
                df.loc[idx1, "wsm_naziv"].notna(), names1
            )

    # 2) fallback: dobavitelj + naziv_ckey (brez enote)
    mask_after_first = _needs_fill(df.get("wsm_sifra"))
    key_fallback = ["sifra_dobavitelja", "naziv_ckey"]
    if mask_after_first.any() and all(
        k in links_df.columns for k in key_fallback
    ):
        lk2_cols = (
            key_fallback + ["wsm_sifra"] + ([name_col] if name_col else [])
        )
        lk2 = (
            links_df[lk2_cols]
            .dropna(subset=["wsm_sifra"])
            .drop_duplicates(key_fallback)
        )
        m2 = df.loc[mask_after_first, key_fallback].merge(
            lk2, on=key_fallback, how="left"
        )
        idx2 = df.index[mask_after_first]
        codes2 = pd.Series(m2["wsm_sifra"].values, index=idx2)
        df.loc[idx2, "wsm_sifra"] = df.loc[idx2, "wsm_sifra"].where(
            ~_needs_fill(df.loc[idx2, "wsm_sifra"]), codes2
        )
        if name_col:
            names2 = pd.Series(m2[name_col].values, index=idx2)
            df.loc[idx2, "wsm_naziv"] = df.loc[idx2, "wsm_naziv"].where(
                df.loc[idx2, "wsm_naziv"].notna(), names2
            )

    mask_final = _needs_fill(df.get("wsm_sifra"))
    after = int((~mask_final).sum())
    updated = max(0, after - before)

    st = df.get("status")
    if st is None:
        df["status"] = None
        st = df["status"]
    empty_status = st.fillna("").astype(str).str.strip().eq("")
    filled_mask = mask_initial & ~mask_final
    df.loc[filled_mask & empty_status, "status"] = "AUTO • povezava"

    return df, updated


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
    df["eff_discount_pct"] = df["eff_discount_pct"].fillna(Decimal("0"))
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
                return (
                    (p_before - p_after) / p_before * Decimal("100")
                ).quantize(Decimal("0.01"), ROUND_HALF_UP)
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
    """True, če je vrstica KNJIŽENA (status POVEZANO ali AUTO)."""
    if isinstance(df_or_sr, pd.DataFrame) and "status" in df_or_sr.columns:
        st = df_or_sr["status"].fillna("").astype(str).str.upper().str.strip()
        mask = st.str.startswith(("POVEZANO", "AUTO"))
        # Če je status prazen, a je WSM šifra vnešena, štej kot knjiženo
        try:
            col = first_existing_series(df_or_sr, ["wsm_sifra", "WSM šifra"])
        except Exception:
            col = None
        if col is not None:
            s = col.astype("string").map(_norm_wsm_code)
            excluded = _excluded_codes_upper()
            mask = mask | (st.str.strip().eq("") & ~s.isin(excluded))
        return mask
    if isinstance(df_or_sr, pd.Series):
        sr = df_or_sr
    else:
        # Primarno uporabljaj grid-stolpec; display kopija je lahko
        # nesinhronizirana
        col = first_existing_series(df_or_sr, ["wsm_sifra", "WSM šifra"])
        if col is None:
            return pd.Series(False, index=df_or_sr.index)
        sr = col
    s = sr.astype("string").map(_norm_wsm_code)
    excluded = _excluded_codes_upper()
    return ~s.isin(excluded)


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
        # uporabi efektivni rabat; če ga ni, vzemi surovega
        # (negativno ničlo sproti počistimo)
        pct = row.get("eff_discount_pct", row.get("rabata_pct", Decimal("0")))
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

    log.info(
        "AUTO_APPLY_LINKS=%s → shranjene povezave %s.",
        AUTO_APPLY_LINKS,
        "BODO uveljavljene" if AUTO_APPLY_LINKS else "NE bodo",
    )

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
            "Število prebranih povezav iz %s: %d", links_file, len(manual_old)
        )
        log.debug(
            "Primer povezav iz %s: %s", links_file, manual_old.head().to_dict()
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

    links_df = manual_old
    df["naziv_ckey"] = df["naziv"].map(_clean)
    globals()["_PENDING_LINKS_DF"] = links_df
    log.info("AUTO_APPLY_LINKS=%s", AUTO_APPLY_LINKS)
    if AUTO_APPLY_LINKS:
        try:
            df, upd_cnt = _apply_links_to_df(df, links_df)
            df = _fill_names_from_catalog(df, wsm_df)
            df = _normalize_wsm_display_columns(df)
            globals()["_CURRENT_GRID_DF"] = df
            log.info(
                "Samodejno uveljavljene povezave: %d vrstic posodobljenih.",
                upd_cnt,
            )
        except Exception as e:
            log.exception("Napaka pri auto-uveljavitvi povezav: %s", e)
    else:
        log.info(
            "AUTO_APPLY_LINKS=0 → shranjene povezave NE bodo "
            "uveljavljene samodejno."
        )

    # Poskrbi za prisotnost in tipe stolpcev za WSM povezave
    for c in ("wsm_sifra", "wsm_naziv"):
        if c not in df.columns:
            df[c] = pd.Series(pd.NA, index=df.index, dtype="string")
        else:
            df[c] = df[c].astype("string")

    # Enotno ime prikaznih stolpcev v gridu (tudi ko AUTO_APPLY_LINKS=0)
    df = _normalize_wsm_display_columns(df)

    if not AUTO_APPLY_LINKS:
        if "status" not in df.columns:
            df["status"] = ""
        mask_not_booked = df["status"].astype(str).str.upper().ne("POVEZANO")
        df.loc[mask_not_booked, ["wsm_sifra", "wsm_naziv"]] = pd.NA

    # Po morebitnem praznjenju ponovno poravnaj prikazne vrednosti
    df = _normalize_wsm_display_columns(df)

    df["multiplier"] = Decimal("1")
    log.debug(f"df po inicializaciji: {df.head().to_dict()}")

    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    # poskrbi, da je df_doc skladen z df
    df_doc = _normalize_wsm_display_columns(df_doc)
    df_doc = df_doc.loc[:, ~df_doc.columns.duplicated()].copy()

    doc_discount_raw = df_doc["vrednost"].sum()
    doc_discount = (
        doc_discount_raw
        if isinstance(doc_discount_raw, Decimal)
        else Decimal(str(doc_discount_raw))
    )
    log.debug("df before _DOC_ filter:\n%s", df.to_string())
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
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

    for _c in ("vrednost", "rabata"):
        df[_c] = df[_c].apply(
            lambda x: x if isinstance(x, Decimal) else Decimal(str(x))
        )
    df["rabata_pct"] = df.apply(
        lambda r: (
            (
                r["rabata"] / (r["vrednost"] + r["rabata"]) * Decimal("100")
            ).quantize(Decimal("0.01"), ROUND_HALF_UP)

            if r["vrednost"] != 0 and (r["vrednost"] + r["rabata"]) != 0

            else Decimal("0")
        ),
        axis=1,
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
    if os.environ.get("WSM_HIDE_HEADER_LINES", "1") != "0":
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
        import pandas as pd

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
                    df.loc[mask, "_booked_sifra"] = cur[mask]
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
        ("vrnjeno", "Vrnjeno"),
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
        import pandas as pd

        try:
            s = (
                df["wsm_sifra"].fillna("").astype(str).str.strip().str.upper()
                if "wsm_sifra" in df.columns
                else pd.Series("", index=df.index)
            )
            excluded = _excluded_codes_upper()
            by_code = s.ne("") & ~s.isin(excluded)

            by_status = (
                df["status"]
                .fillna("")
                .astype(str)
                .str.upper()
                .str.startswith(("POVEZANO", "AUTO"))
                if "status" in df.columns
                else pd.Series(False, index=df.index)
            )

            booked_mask = by_code | by_status

            # nikoli ne štej kot knjiženo, če je še vedno “OSTALO”
            if "wsm_naziv" in df.columns:
                nm = (
                    df["wsm_naziv"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )
                booked_mask &= nm != "OSTALO"

            booked = int(booked_mask.sum())
            remaining = int(len(df) - booked)
            return booked, remaining
        except Exception:
            return 0, len(df)

    def _update_summary():
        import pandas as pd
        from decimal import Decimal, ROUND_HALF_UP
        from wsm.ui.review.helpers import _norm_wsm_code as _norm_code

        # privzeto grupiraj po rabatu
        globals().setdefault("GROUP_BY_DISCOUNT", True)

        df = globals().get("_CURRENT_GRID_DF")
        if df is None:
            df = globals().get("df")
        df = (
            df.loc[:, ~df.columns.duplicated()].copy()
            if df is not None
            else None
        )
        if df is not None:
            dups = df.columns[df.columns.duplicated()].tolist()
            if dups:
                log.warning(
                    "SUMMARY: duplicated columns still present: %s", dups
                )
        if df is None or df.empty:
            _render_summary(summary_df_from_records([]))
            globals()["_SUMMARY_COUNTS"] = (0, 0)
            return

        def _col(frame, column):
            if column not in frame.columns:
                import pandas as pd

                return pd.Series([None] * len(frame), index=frame.index)
            s = frame[column]
            return s.iloc[:, 0] if hasattr(s, "ndim") and s.ndim == 2 else s

        # --- KOALESCENCA KODE: _booked_sifra → wsm_sifra →
        #     "WSM šifra" → _summary_key ---
        b = (
            _col(df, "_booked_sifra")
            if "_booked_sifra" in df.columns
            else None
        )
        f = first_existing_series(
            df, ["wsm_sifra", "WSM šifra", "_summary_key"]
        )
        if b is None:
            code_s = (
                f
                if f is not None
                else pd.Series([""] * len(df), index=df.index)
            )
        else:
            code_s = b.astype("string")
            if f is not None:
                f = f.astype("string")
                empty_b = code_s.fillna("").str.strip().eq("")
                code_s = code_s.where(~empty_b, f)

        code_s = code_s.astype("string").fillna("").map(_norm_code)
        df["_summary_key"] = code_s  # poravnava summary ključev

        excluded = _excluded_codes_upper()
        is_booked = ~code_s.str.upper().isin(excluded)
        df["_is_booked"] = is_booked
        code_or_ostalo = code_s.where(is_booked, "OSTALO")

        unit_s = first_existing_series(df, ["enota_norm", "enota"])
        if unit_s is None:
            unit_s = pd.Series([""] * len(df), index=df.index)
        unit_s = unit_s.astype(object).fillna("").map(str).str.strip()

        # Rabat za grouping: najprej rabata_pct, sicer eff_discount_pct
        def _to_dec(x):
            try:
                return x if isinstance(x, Decimal) else Decimal(str(x))
            except Exception:
                return Decimal("0")

        if "rabata_pct" in df.columns:
            rab_s = _col(df, "rabata_pct").apply(_to_dec)
        else:
            rab_s = _col(df, "eff_discount_pct").apply(_to_dec)

        def _q2p(d: Decimal) -> Decimal:
            q = d.quantize(Decimal("0.01"), ROUND_HALF_UP)
            return Decimal("0.00") if q == 0 else q

        rab_s = rab_s.map(_q2p)
        if not globals().get("GROUP_BY_DISCOUNT", True):
            rab_s[:] = Decimal("0.00")

        df["_summary_gkey"] = list(
            zip(code_or_ostalo.tolist(), unit_s.tolist(), rab_s.tolist())
        )

        # Ensure eff_discount_pct
        try:
            _ensure_eff_discount_pct(df)
        except NameError:
            pass

        # Priprava polj
        val_s = first_existing_series(
            df, ["Neto po rabatu", "Skupna neto", "vrednost", "total_net"]
        )
        bruto_s = first_existing_series(
            df, ["Bruto", "vrednost_bruto", "Skupna bruto", "vrednost"]
        )
        qty_s = first_existing_series(df, ["Količina", "kolicina_norm"])

        # Za naziv uporabljamo isto koalescentno kodo
        wsm_s = code_s

        # Naziv (serija)
        if "WSM Naziv" in df.columns:
            name_s = _col(df, "WSM Naziv").astype("string")
        elif "WSM naziv" in df.columns:
            name_s = _col(df, "WSM naziv").astype("string")
        elif "wsm_naziv" in df.columns:
            name_s = _col(df, "wsm_naziv").astype("string")
        else:
            name_s = pd.Series([""] * len(df), index=df.index, dtype="string")

        if wsm_s is not None:
            wsm_s = wsm_s.map(_norm_code).astype("string")

        name_s = name_s.astype("string").fillna("")
        if wsm_s is not None:
            _m = wsm_s.astype(str).eq("OSTALO")
            if _m.any():
                name_s = name_s.where(~_m, "Ostalo")

        if wsm_s is None or val_s is None:
            _render_summary(summary_df_from_records([]))
            return

        eff_s = (
            _col(df, "eff_discount_pct")
            if "eff_discount_pct" in df.columns
            else pd.Series([Decimal("0")] * len(df), index=df.index)
        )

        data = {
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
            "eff_discount_pct": eff_s,
            "_summary_gkey": _col(df, "_summary_gkey"),
        }
        if "status" in df.columns:
            data["status"] = _col(df, "status")

        # Varna konstrukcija DataFrame-a
        n = len(df)

        def _to_list(x):
            if isinstance(x, pd.Series):
                return x.reindex(df.index).tolist()
            if isinstance(x, pd.Index):
                return pd.Series(x).reindex(df.index).tolist()
            try:
                import numpy as np

                if isinstance(x, np.ndarray):
                    x = x.reshape(-1).tolist()
            except Exception:
                pass
            if isinstance(x, (list, tuple)):
                arr = list(x)
            else:
                arr = [x]
            if len(arr) == n:
                return arr
            if len(arr) == 1:
                return arr * n
            if len(arr) < n:
                return arr + [None] * (n - len(arr))
            return arr[:n]

        data = {k: _to_list(v) for k, v in data.items()}
        work = pd.DataFrame(data)

        # knjiženost
        try:
            if "status" in work.columns:
                work["_is_booked"] = (
                    _col(work, "status")
                    .fillna("")
                    .astype(str)
                    .str.upper()
                    .eq("POVEZANO")
                ).astype(int)
            else:
                work["_is_booked"] = _booked_mask_from(work).astype(int)
        except Exception:
            _ws = _col(work, "wsm_sifra").fillna("").astype(str).str.strip()
            work["_is_booked"] = _ws.ne("") & ~_ws.str.upper().isin(
                _excluded_codes_upper()
            )

        if globals().get("ONLY_BOOKED_IN_SUMMARY"):
            work = work[work["_is_booked"] > 0]
            if work.empty:
                _render_summary(summary_df_from_records([]))
                return

        from decimal import Decimal as _D

        def dsum(s):
            tot = _D("0")
            for v in s:
                try:
                    tot += v if isinstance(v, _D) else _D(str(v))
                except Exception:
                    pass
            return tot

        def dsum_neg(s):
            tot = _D("0")
            for v in s:
                try:
                    dv = v if isinstance(v, _D) else _D(str(v))
                    if dv < 0:
                        tot += abs(dv)
                except Exception:
                    pass
            return tot

        df_b = work.copy()
        groups = list(df_b.groupby("_summary_gkey", dropna=False))

        # katalog za fallback imena
        try:
            _sdf = globals().get("sifre_df") or globals().get("wsm_df")
            _CODE2NAME = (
                (
                    _sdf.assign(
                        wsm_sifra=_col(_sdf, "wsm_sifra")
                        .astype(str)
                        .str.strip(),
                        wsm_naziv=_col(_sdf, "wsm_naziv").astype(str),
                    )
                    .dropna(subset=["wsm_naziv"])
                    .drop_duplicates("wsm_sifra")
                    .set_index("wsm_sifra")["wsm_naziv"]
                    .to_dict()
                )
                if _sdf is not None
                and {"wsm_sifra", "wsm_naziv"}.issubset(_sdf.columns)
                else {}
            )
        except Exception as _e:
            log.warning("SUMMARY name map build failed: %s", _e)
            _CODE2NAME = {}

        records = []
        for key, g in groups:
            code, _, rab = key
            is_booked = code != "OSTALO"
            show_code = code if is_booked else "OSTALO"

            disp_name = ""
            nm_s = first_existing_series(
                g, ["WSM Naziv", "WSM naziv", "wsm_naziv"]
            )
            if nm_s is not None:
                _nm = nm_s.astype(str).str.strip()
                _nm = _nm[(_nm != "") & (_nm.str.lower() != "ostalo")]
                if len(_nm):
                    disp_name = _nm.iloc[0]

            if disp_name == "" and is_booked:
                code_str = str(code).strip()
                disp_name = _CODE2NAME.get(code_str, "")

            qty_series = first_existing_series(
                g, ["kolicina_norm", "Količina", "kolicina"]
            )
            qty_total = dsum(qty_series)
            qty_ret = dsum_neg(qty_series)

            records.append(
                {
                    "WSM šifra": show_code,
                    "WSM Naziv": disp_name if is_booked else "Ostalo",
                    "Količina": qty_total,
                    "Vrnjeno": qty_ret,
                    "Znesek": (
                        dsum(_col(g, "bruto"))
                        if bruto_s is not None
                        else dsum(_col(g, "znesek"))
                    ),
                    "Rabat (%)": rab,
                    "Neto po rabatu": dsum(_col(g, "znesek")),
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
            _ws = (
                _col(df_summary, "WSM šifra")
                .fillna("")
                .astype(str)
                .str.strip()
            )
            bm = _ws.ne("") & ~_ws.str.upper().isin(_excluded_codes_upper())
        booked_mask_new = bm

        # Backfill imen po konsolidaciji, če je še prazno/"Ostalo"
        try:
            sdf = globals().get("sifre_df") or globals().get("wsm_df")
            if sdf is not None and {"wsm_sifra", "wsm_naziv"}.issubset(
                sdf.columns
            ):
                code2name = (
                    sdf.assign(
                        wsm_sifra=_col(sdf, "wsm_sifra")
                        .astype(str)
                        .str.strip(),
                        wsm_naziv=_col(sdf, "wsm_naziv").astype(str),
                    )
                    .dropna(subset=["wsm_naziv"])
                    .drop_duplicates("wsm_sifra")
                    .set_index("wsm_sifra")["wsm_naziv"]
                )
                names = df_summary["WSM Naziv"].astype(str)
                booked_mask_new = (
                    df_summary["WSM šifra"].astype(str).str.strip().ne("")
                )
                still_empty = names.str.strip().eq("") | names.str.lower().eq(
                    "ostalo"
                )
                mask = booked_mask_new & still_empty
                df_summary.loc[mask, "WSM Naziv"] = (
                    df_summary.loc[mask, "WSM šifra"]
                    .astype(str)
                    .str.strip()
                    .map(code2name)
                    .fillna(df_summary.loc[mask, "WSM Naziv"])
                )
        except Exception as e:
            log.warning("WSM Naziv backfill from catalog failed: %s", e)

        b, u = globals().get(
            "_fallback_count_from_grid", lambda df: (0, len(df))
        )(df)
        globals()["_SUMMARY_COUNTS"] = (b, u)
        try:
            sum_booked_var.set(f"Knjiženo: {b}")
            sum_unbooked_var.set(f"Ostane: {u}")
        except Exception:
            pass

        df_summary = df_summary.loc[:, ~df_summary.columns.duplicated()].copy()
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
    _status_var = tk.StringVar(value="")
    status_count_label = ttk.Label(total_frame, textvariable=_status_var)
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
        discount = doc_discount
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
            indicator_label.config(
                text="✓" if difference <= tolerance else "✗",
                style=(
                    "Indicator.Green.TLabel"
                    if difference <= tolerance
                    else "Indicator.Red.TLabel"
                ),
            )
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
            except Exception as e:
                log.warning("Osvežitev grid celic ni uspela: %s", e)
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
    if AUTO_APPLY_LINKS:
        try:
            root.after(0, lambda: _apply_saved_links_now(_silent=True))
        except Exception as e:
            log.debug("AUTO refresh WSM stolpcev v gridu preskočen: %s", e)

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

        _show_tooltip(sel_i, tooltip)
        if "is_gratis" in df.columns and df.at[idx, "is_gratis"]:
            tset = set(tree.item(sel_i).get("tags", ()))
            tset.add("gratis")
            tree.item(sel_i, tags=tuple(tset))
            tree.set(sel_i, "warning", "GRATIS")

        def _safe_cell(idx, c, default=""):
            # Ne zaupaj, da stolpec vedno obstaja po merge/urejanju
            if c not in df.columns:
                return default
            try:
                v = df.at[idx, c]
            except Exception:
                return default
            if isinstance(v, (Decimal, float, int)) and not isinstance(
                v, bool
            ):
                return _fmt(_clean_neg_zero(v))
            if v is None or (hasattr(pd, "isna") and pd.isna(v)):
                return ""
            return str(v)

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
        if "WSM Naziv" in df.columns:
            df.at[idx, "WSM Naziv"] = ""
        try:
            tree.set(sel_i, "WSM šifra", "")
            tree.set(sel_i, "WSM Naziv", "")
        except Exception:
            pass
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
