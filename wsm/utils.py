# File: wsm/utils.py
# -*- coding: utf-8 -*-
"""
WSM helper module – združevanje postavk, samodejno povezovanje z WSM kodami,
shranjevanje zgodovine cen, ipd.
"""
from __future__ import annotations

from pathlib import Path
from decimal import Decimal
import re
from typing import Tuple, Union, List, Dict

import pandas as pd
from wsm.ui.review_links import _load_supplier_map

import logging
log = logging.getLogger(__name__)

# ────────────────────────── skupna orodja ───────────────────────────
def sanitize_folder_name(name: str) -> str:
    """Return a Windows- and Linux-safe folder name.

    Prepovedane znake zamenja z ``_`` in odstrani končne presledke ali pike.
    Odstrani tudi kontrolne znake (ASCII < 32). Poleg tega prepozna
    rezervirana imena v Windows (npr. ``CON``, ``PRN``) in jim doda ``_`` na
    konec, da se izogne napakam pri ustvarjanju map.
    """

    if not isinstance(name, str):
        raise TypeError(
            f"sanitize_folder_name expects a string, got {type(name)}"
        )
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    cleaned = re.sub(r'[\x00-\x1f]', "_", cleaned)

    # Trailing dots and spaces niso dovoljeni na Windows
    cleaned = re.sub(r"[\s.]+$", "", cleaned)

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }

    if cleaned.upper() in reserved:
        cleaned += "_"


    return cleaned


def _clean(s: str) -> str:
    """Normalize whitespace and lowercase the string."""
    return re.sub(r"\s+", " ", s.strip().lower())

# Helper to retrieve the first real supplier code from a DataFrame. ``_DOC_``
# rows appear in some invoices due to document-level discounts and should be
# ignored when determining the main supplier.
def main_supplier_code(df: pd.DataFrame) -> str:
    """Return the first ``sifra_dobavitelja`` value that isn't ``"_DOC_"``."""
    if df.empty or "sifra_dobavitelja" not in df.columns:
        return ""

    for code in df["sifra_dobavitelja"]:
        if str(code) != "_DOC_":
            return str(code)
    return str(df["sifra_dobavitelja"].iloc[0])

# ────────────────────────── združevanje postavk ─────────────────────
def zdruzi_artikle(df: pd.DataFrame) -> pd.DataFrame:
    """
    Združi identične artikle (šifra dobavitelja + naziv + rabat), sešteje količine
    ter preračuna cene. Dokumentarne popuste (“_DOC_”) ohrani nespremenjene.
    """
    if df.empty:
        return df
    doc_mask = df["sifra_dobavitelja"] == "_DOC_"
    df_doc   = df[doc_mask].copy()
    df_rest  = df[~doc_mask].copy()

    grouped = (df_rest
        .groupby(["sifra_dobavitelja", "naziv", "rabata", "rabata_pct"],
                 dropna=False, as_index=False)
        .agg({"enota": "first", "kolicina": "sum", "vrednost": "sum"}))
    grouped["cena_netto"] = grouped.apply(
        lambda r: r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0"), axis=1)
    grouped["cena_bruto"] = grouped["cena_netto"]

    grouped = grouped[[
        "sifra_dobavitelja", "naziv", "kolicina", "enota",
        "cena_bruto", "cena_netto", "vrednost", "rabata", "rabata_pct"
    ]]
    return pd.concat([grouped, df_doc], ignore_index=True)

# ────────────────────────── pomožne tabele ──────────────────────────
def _coerce_keyword_column(df: pd.DataFrame) -> pd.DataFrame:
    """Dovoli alternativno ime stolpca (“kljucna_beseda”) namesto “keyword”."""
    if "keyword" in df.columns:
        return df
    for col in df.columns:
        if col.strip().lower() == "kljucna_beseda":
            return df.rename(columns={col: "keyword"})
    return df

def extract_keywords(links_dir: Path, keywords_path: Path) -> pd.DataFrame:
    """Prebere ročne povezave in iz njih izdela seznam ključnih besed."""
    rows: List[Dict[str, str]] = []
    token_rx = re.compile(r"\b\w+\b")

    for path in links_dir.glob("*/*_povezane.xlsx"):
        try:
            df = pd.read_excel(path, dtype=str)
        except Exception as exc:
            log.warning(f"Ne morem prebrati {path}: {exc}")
            continue
        if "wsm_sifra" not in df.columns or "naziv" not in df.columns:
            continue

        for code, names in df.dropna(subset=["wsm_sifra", "naziv"]).groupby("wsm_sifra")[
            "naziv"
        ]:
            cnt: Dict[str, int] = {}
            for n in names:
                for t in token_rx.findall(str(n).lower()):
                    if len(t) < 3:
                        continue
                    cnt[t] = cnt.get(t, 0) + 1
            for token, c in cnt.items():
                if c >= 2:
                    rows.append({"wsm_sifra": code, "keyword": token})

    kw_df = pd.DataFrame(rows)
    if not kw_df.empty:
        kw_df.drop_duplicates(inplace=True)
        kw_df.sort_values(["wsm_sifra", "keyword"], inplace=True)

    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    if keywords_path.exists():
        try:
            old = pd.read_excel(keywords_path, dtype=str)
            old = _coerce_keyword_column(old)
            kw_df = pd.concat([old[["wsm_sifra", "keyword"]], kw_df], ignore_index=True)
            kw_df.drop_duplicates(inplace=True)
        except Exception as exc:
            log.warning(f"Napaka pri branju obstoječih ključnih besed: {exc}")

    kw_df.to_excel(keywords_path, index=False)
    return kw_df

def load_wsm_data(
    sifre_path   : str,
    keywords_path: str,
    links_dir    : Path,
    supplier_code: str
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Vrne:
      • sifre_df  – celotno tabelo “sifre_wsm.xlsx”
      • kw_df     – ključne besede filtrirane na dobavitelja, če je stolpec
      • links_df  – ročne povezave za dobavitelja (če obstaja datoteka)
    """
    sifre_df = pd.read_excel(sifre_path, dtype=str)

    kw_all = pd.read_excel(keywords_path, dtype=str)
    kw_all = _coerce_keyword_column(kw_all)
    if "sifra_dobavitelja" in kw_all.columns:
        kw_df = kw_all[kw_all["sifra_dobavitelja"] == supplier_code][["wsm_sifra", "keyword"]]
    else:
        kw_df = kw_all[["wsm_sifra", "keyword"]] if "keyword" in kw_all.columns else pd.DataFrame(columns=["wsm_sifra", "keyword"])

    suppliers_file = links_dir
    sup_map = _load_supplier_map(suppliers_file)
    
    supplier_info = sup_map.get(supplier_code, {})
    supplier_name = supplier_info.get('ime', supplier_code) if isinstance(supplier_info, dict) else supplier_code
    safe_name = sanitize_folder_name(supplier_name)

    links_path = links_dir / safe_name / f"{supplier_code}_{safe_name}_povezane.xlsx"
    if links_path.exists():
        links_df = pd.read_excel(links_path, dtype=str)
    else:
        links_df = pd.DataFrame(columns=["sifra_dobavitelja", "naziv", "naziv_ckey", "wsm_sifra"])

    return sifre_df, kw_df, links_df

# ────────────────────────── samodejno povezovanje ───────────────────
def povezi_z_wsm(
    df_items      : pd.DataFrame,
    sifre_path    : str,
    keywords_path : str,
    links_dir     : Path,
    supplier_code : str
) -> pd.DataFrame:
    """
    Poskusi vsaki vrstici v df_items pripisati WSM kodo:
      1) če obstaja ročna povezava → status “POVEZANO”
      2) če se v nazivu pojavi ključna beseda → status “KLJUCNA_BES”
      3) sicer status NaN (prazno)
    Nove zadetke po ključnih besedah doda v datoteko povezav.
    """
    kw_path = Path(keywords_path)
    if not kw_path.exists():
        extract_keywords(links_dir, kw_path)

    _, kw_df, manual_links = load_wsm_data(
        sifre_path, str(kw_path), links_dir, supplier_code
    )

    if kw_df.empty:
        kw_df = extract_keywords(links_dir, kw_path)

    df_items = df_items.copy()
    df_items["naziv_ckey"]     = df_items["naziv"].map(_clean)
    manual_links["naziv_ckey"] = manual_links["naziv"].map(_clean)

    df = df_items.merge(
        manual_links[["sifra_dobavitelja", "naziv_ckey", "wsm_sifra"]],
        on=["sifra_dobavitelja", "naziv_ckey"], how="left"
    )
    df["status"] = df["wsm_sifra"].notna().map({True: "POVEZANO", False: pd.NA})

    new_links: List[Dict] = []
    mask = df["status"].isna()
    if not kw_df.empty:
        for idx, row in df[mask].iterrows():
            text = row["naziv"].lower()
            hit  = kw_df[kw_df["keyword"].str.lower().apply(lambda k: k in text)]
            if not hit.empty:
                wsm_code = hit.iloc[0]["wsm_sifra"]
                df.at[idx, "wsm_sifra"] = wsm_code
                df.at[idx, "status"]    = "KLJUCNA_BES"
                new_links.append({
                    "sifra_dobavitelja": row["sifra_dobavitelja"],
                    "naziv":            row["naziv"],
                    "naziv_ckey":       row["naziv_ckey"],
                    "wsm_sifra":        wsm_code,
                })

    # če so novosti → posodobi datoteko povezav
    if new_links:
        suppliers_file = links_dir
        sup_map = _load_supplier_map(suppliers_file)
        supplier_info = sup_map.get(supplier_code, {})
        supplier_name = supplier_info.get('ime', supplier_code) if isinstance(supplier_info, dict) else supplier_code
        safe_name = sanitize_folder_name(supplier_name)

        dst = links_dir / safe_name
        dst.mkdir(parents=True, exist_ok=True)
        links_path = dst / f"{supplier_code}_{safe_name}_povezane.xlsx"

        manual_links = pd.concat([manual_links, pd.DataFrame(new_links)], ignore_index=True)
        manual_links.drop_duplicates(
            subset=["sifra_dobavitelja", "naziv_ckey"], keep="first", inplace=True
        )
        manual_links.to_excel(links_path, index=False)

    return df

# ────────────────────────── export & log ────────────────────────────
def export_to_excel(df: pd.DataFrame, filename: str) -> None:
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(filename, index=False)

def log_price_history(
    df: pd.DataFrame,
    history_file: Union[str, Path],
    *,
    service_date: str | None = None,
    suppliers_dir: Union[str, Path] | None = None,
    max_entries_per_code: int = 50,
    invoice_id: str | None = None,
) -> None:
    """
    Zapiše zgodovino cen v ``links/<ime_dobavitelja>/price_history.xlsx``.
    Shranjeni so identifikator artikla (``sifra_dobavitelja + naziv``), cena,
    trenutni čas in opcijsko datum opravljene storitve.
    """
    suppliers_path = Path(suppliers_dir) if suppliers_dir is not None else Path("links")
    sup_map = _load_supplier_map(suppliers_path)

    df["supplier_name"] = df["sifra_dobavitelja"].apply(
        lambda x: sup_map.get(str(x), {}).get('ime', str(x))
    )
    primary_code = main_supplier_code(df)
    primary_name = df[df["sifra_dobavitelja"] == primary_code]["supplier_name"].iloc[0] if primary_code else df["supplier_name"].iloc[0]
    safe_name = sanitize_folder_name(primary_name)

    history_path = Path(history_file).parent / safe_name / "price_history.xlsx"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    # Ustvari ključ iz sifra_dobavitelja in naziv
    df_hist = df[["sifra_dobavitelja", "naziv", "cena_bruto"]].copy()
    df_hist["key"] = df_hist["sifra_dobavitelja"] + "_" + df_hist["naziv"].str.replace(r"[^\w\s]", "_", regex=True)
    df_hist = df_hist[["key", "cena_bruto"]].copy()
    df_hist.columns = ["key", "cena"]
    df_hist["time"] = pd.Timestamp.now()
    df_hist["service_date"] = service_date
    df_hist["invoice_id"] = invoice_id

    # Preveri, ali so podatki pravilni
    if df_hist["key"].isna().any() or df_hist["key"].str.strip().eq("").any():
        log.warning("Nekateri ključi v zgodovini cen so prazni ali neveljavni!")
    log.debug(f"Zgodovina cen: {df_hist.head().to_dict()}")

    if history_path.exists():
        old = pd.read_excel(history_path, dtype={"key": str})
        if "invoice_id" not in old.columns:
            old["invoice_id"] = pd.NA
        if invoice_id is not None:
            mask = (old["invoice_id"] == invoice_id) & (old["key"].isin(df_hist["key"]))
            old = old[~mask]
        df_hist = pd.concat([old, df_hist], ignore_index=True)

    df_hist = (
        df_hist.sort_values("time", ascending=False)
               .groupby("key", as_index=False)
               .head(max_entries_per_code)
    )
    df_hist.to_excel(history_path, index=False)
