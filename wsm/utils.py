# File: wsm/utils.py
# -*- coding: utf-8 -*-
"""
WSM helper module – združevanje postavk, samodejno povezovanje z WSM kodami,
shranjevanje zgodovine cen, ipd.
"""
from __future__ import annotations

from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import os
import re
from typing import Tuple, Union, List, Dict

import pandas as pd
import logging

log = logging.getLogger(__name__)


def _load_supplier_map(path: Path) -> dict:
    """Lazy import wrapper for :func:`wsm.supplier_store.load_suppliers`."""
    from wsm.supplier_store import load_suppliers as real

    return real(path)


# ────────────────────────── skupna orodja ───────────────────────────
def sanitize_folder_name(name: str) -> str:
    """Return a Windows- and Linux-safe folder name.

    Prepovedane znake zamenja z ``_`` in odstrani končne presledke ali pike.
    Odstrani tudi kontrolne znake (ASCII < 32). Poleg tega prepozna
    rezervirana imena v Windows (npr. ``CON``, ``PRN``) in jim doda ``_`` na
    konec, da se izogne napakam pri ustvarjanju map.  Če je po čiščenju
    rezultat prazen, vrne ``"unknown"``.
    """

    if not isinstance(name, str):
        raise TypeError(
            f"sanitize_folder_name expects a string, got {type(name)}"
        )
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    cleaned = re.sub(r"[\x00-\x1f]", "_", cleaned)

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

    if cleaned == "":
        return "unknown"

    return cleaned


def _clean(s: str) -> str:
    """Normalize whitespace and lowercase the string."""
    return re.sub(r"\s+", " ", s.strip().lower())


def short_supplier_name(name: str) -> str:
    """Return a supplier name without location or extra descriptors.

    Examples
    --------
    >>> short_supplier_name("Podjetje d.o.o., Maribor")
    'Podjetje d.o.o.'
    >>> short_supplier_name("Dobavitelj d.d. Celje")
    'Dobavitelj d.d.'
    """

    if not isinstance(name, str):
        return name

    base = name.split(",")[0]
    m = re.search(r"(.+?(?:d\.o\.o\.|d\.d\.|s\.p\.))", base, re.I)
    if m:
        base = m.group(1)
    return base.strip()


def _build_header_totals(
    invoice_path: Path | None, invoice_total: Decimal
) -> dict[str, Decimal]:
    """Return header ``net``, ``vat`` and ``gross`` amounts.

    When ``invoice_path`` points to an XML invoice, the values are read
    from the file.  If the extracted ``net`` is zero while both ``gross``
    and ``vat`` are non-zero, ``net`` is replaced with ``gross - vat`` to
    handle certain malformed invoices.  On any error or when the path is
    not an XML file, ``invoice_total`` is used as the net and gross
    amount and VAT defaults to ``0``.
    """

    totals = {
        "net": invoice_total,
        "vat": Decimal("0"),
        "gross": invoice_total,
    }

    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import (
                extract_header_net,
                extract_total_tax,
                extract_header_gross,
                DEC2,
            )

            net = extract_header_net(invoice_path)
            vat = extract_total_tax(invoice_path)
            gross = extract_header_gross(invoice_path)

            if net == 0 and vat != 0 and gross != 0:
                net = (gross - vat).quantize(DEC2, ROUND_HALF_UP)

            if gross == 0 and net != 0 and vat != 0:
                gross = (net + vat).quantize(DEC2, ROUND_HALF_UP)

            if vat == 0 and net != 0 and gross != 0:
                vat = (gross - net).quantize(DEC2, ROUND_HALF_UP)

            totals = {"net": net, "vat": vat, "gross": gross}
        except Exception as exc:  # pragma: no cover - robust against IO
            log.warning(f"Napaka pri branju zneskov glave: {exc}")

    invoice_total = totals["net"]

    log.debug(
        "HEADER  %s  ⇒  net=%s  vat=%s  gross=%s",
        invoice_path,
        totals["net"],
        totals["vat"],
        totals["gross"],
    )
    return totals


# Helper to retrieve the first real supplier code from a DataFrame. ``_DOC_``
# rows appear in some invoices due to document-level discounts and should be
# ignored when determining the main supplier.
def main_supplier_code(df: pd.DataFrame) -> str:
    """Return the first ``sifra_dobavitelja`` that isn't ``"_DOC_"``,
    blank or NaN."""

    if df.empty or "sifra_dobavitelja" not in df.columns:
        return ""

    for code in df["sifra_dobavitelja"]:
        if pd.isna(code) or str(code).strip() == "" or str(code) == "_DOC_":
            continue
        return str(code)

    return ""


# ────────────────────────── združevanje postavk ─────────────────────
def zdruzi_artikle(df: pd.DataFrame) -> pd.DataFrame:
    """
    Združi identične artikle (šifra dobavitelja + naziv + rabat), sešteje
    količine ter preračuna cene. Dokumentarne popuste (“_DOC_”) ohrani
    nespremenjene.
    """
    if df.empty:
        return df
    doc_mask = df["sifra_dobavitelja"] == "_DOC_"
    df_doc = df[doc_mask].copy()
    df_rest = df[~doc_mask].copy()

    grouped = df_rest.groupby(
        ["sifra_dobavitelja", "naziv", "rabata", "rabata_pct"],
        dropna=False,
        as_index=False,
    ).agg({"enota": "first", "kolicina": "sum", "vrednost": "sum"})
    grouped["cena_netto"] = grouped.apply(
        lambda r: (
            r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    grouped["cena_bruto"] = grouped["cena_netto"]

    grouped = grouped[
        [
            "sifra_dobavitelja",
            "naziv",
            "kolicina",
            "enota",
            "cena_bruto",
            "cena_netto",
            "vrednost",
            "rabata",
            "rabata_pct",
        ]
    ]
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

        for code, names in df.dropna(subset=["wsm_sifra", "naziv"]).groupby(
            "wsm_sifra"
        )["naziv"]:
            cnt: Dict[str, int] = {}
            for n in names:
                for t in token_rx.findall(str(n).lower()):
                    if len(t) < 4:
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
            kw_df = pd.concat(
                [old[["wsm_sifra", "keyword"]], kw_df], ignore_index=True
            )
            kw_df.drop_duplicates(inplace=True)
        except Exception as exc:
            log.warning(f"Napaka pri branju obstoječih ključnih besed: {exc}")

    kw_df.to_excel(keywords_path, index=False)
    return kw_df


def load_wsm_data(
    sifre_path: str,
    keywords_path: str | None,
    links_dir: Path,
    supplier_code: str,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Vrne:
      • sifre_df  – celotno tabelo “sifre_wsm.xlsx”
      • kw_df     – ključne besede filtrirane na dobavitelja, če je stolpec
      • links_df  – ročne povezave za dobavitelja (če obstaja datoteka)
    """
    sifre_df = pd.read_excel(sifre_path, dtype=str)

    if keywords_path is None:
        keywords_path = os.getenv(
            "WSM_KEYWORDS_FILE", "kljucne_besede_wsm_kode.xlsx"
        )

    kw_all = pd.read_excel(keywords_path, dtype=str)
    kw_all = _coerce_keyword_column(kw_all)
    if "sifra_dobavitelja" in kw_all.columns:
        kw_df = kw_all[kw_all["sifra_dobavitelja"] == supplier_code][
            ["wsm_sifra", "keyword"]
        ]
    else:
        kw_df = (
            kw_all[["wsm_sifra", "keyword"]]
            if "keyword" in kw_all.columns
            else pd.DataFrame(columns=["wsm_sifra", "keyword"])
        )

    suppliers_file = links_dir
    sup_map = _load_supplier_map(suppliers_file)

    supplier_info = sup_map.get(supplier_code, {})
    supplier_name = (
        supplier_info.get("ime", supplier_code)
        if isinstance(supplier_info, dict)
        else supplier_code
    )
    vat_id = (
        supplier_info.get("vat") if isinstance(supplier_info, dict) else None
    )
    safe_id = sanitize_folder_name(vat_id or supplier_name)

    links_path = (
        links_dir / safe_id / f"{supplier_code}_{safe_id}_povezane.xlsx"
    )
    if links_path.exists():
        links_df = pd.read_excel(links_path, dtype=str)
    else:
        links_df = pd.DataFrame(
            columns=["sifra_dobavitelja", "naziv", "naziv_ckey", "wsm_sifra"]
        )

    return sifre_df, kw_df, links_df


# ────────────────────────── samodejno povezovanje ───────────────────
def povezi_z_wsm(
    df_items: pd.DataFrame,
    sifre_path: str,
    keywords_path: str | None = None,
    links_dir: Path | None = None,
    supplier_code: str | None = None,
) -> pd.DataFrame:
    """
    Poskusi vsaki vrstici v ``df_items`` pripisati WSM kodo:
      1) če obstaja ročna povezava → status ``POVEZANO``
      2) če se v nazivu pojavi ključna beseda → status ``KLJUCNA_BES``
      3) sicer status ``NaN`` (prazno)
    Nove zadetke po ključnih besedah doda v datoteko povezav.

    ``keywords_path`` je neobvezen. Če ni podan, funkcija prebere
    okoljsko spremenljivko ``WSM_KEYWORDS_FILE`` in privzeto uporabi
    ``kljucne_besede_wsm_kode.xlsx``.
    """
    if keywords_path is None:
        keywords_path = os.getenv(
            "WSM_KEYWORDS_FILE", "kljucne_besede_wsm_kode.xlsx"
        )
    if links_dir is None or supplier_code is None:
        raise TypeError("links_dir and supplier_code must be provided")
    kw_path = Path(keywords_path)
    if not kw_path.exists():
        extract_keywords(links_dir, kw_path)

    _, kw_df, manual_links = load_wsm_data(
        sifre_path, str(kw_path), links_dir, supplier_code
    )

    if kw_df.empty:
        kw_df = extract_keywords(links_dir, kw_path)

    df_items = df_items.copy()
    df_items["naziv_ckey"] = df_items["naziv"].map(_clean)
    manual_links["naziv_ckey"] = manual_links["naziv"].map(_clean)

    df = df_items.merge(
        manual_links[["sifra_dobavitelja", "naziv_ckey", "wsm_sifra"]],
        on=["sifra_dobavitelja", "naziv_ckey"],
        how="left",
    )
    df["status"] = (
        df["wsm_sifra"].notna().map({True: "POVEZANO", False: pd.NA})
    )

    new_links: List[Dict] = []
    mask = df["status"].isna()
    if not kw_df.empty:
        for idx, row in df[mask].iterrows():
            text = row["naziv"].lower()
            hit = kw_df[
                kw_df["keyword"]
                .str.lower()
                .apply(
                    lambda k: bool(
                        re.search(r"\b%s\b" % re.escape(k.lower()), text)
                    )
                )
            ]
            if not hit.empty:
                wsm_code = hit.iloc[0]["wsm_sifra"]
                df.at[idx, "wsm_sifra"] = wsm_code
                df.at[idx, "status"] = "KLJUCNA_BES"
                new_links.append(
                    {
                        "sifra_dobavitelja": row["sifra_dobavitelja"],
                        "naziv": row["naziv"],
                        "naziv_ckey": row["naziv_ckey"],
                        "wsm_sifra": wsm_code,
                    }
                )

    # če so novosti → posodobi datoteko povezav
    if new_links:
        suppliers_file = links_dir
        sup_map = _load_supplier_map(suppliers_file)
        supplier_info = sup_map.get(supplier_code, {})
        supplier_name = (
            supplier_info.get("ime", supplier_code)
            if isinstance(supplier_info, dict)
            else supplier_code
        )
        vat_id = (
            supplier_info.get("vat")
            if isinstance(supplier_info, dict)
            else None
        )
        safe_id = sanitize_folder_name(vat_id or supplier_name)

        dst = links_dir / safe_id
        dst.mkdir(parents=True, exist_ok=True)
        links_path = dst / f"{supplier_code}_{safe_id}_povezane.xlsx"

        manual_links = pd.concat(
            [manual_links, pd.DataFrame(new_links)], ignore_index=True
        )
        manual_links.drop_duplicates(
            subset=["sifra_dobavitelja", "naziv_ckey"],
            keep="first",
            inplace=True,
        )
        manual_links.to_excel(links_path, index=False)
        extract_keywords(links_dir, kw_path)

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

    Parameters
    ----------
    overwrite : bool, optional
        When ``True`` all rows with the same ``invoice_id`` are removed
        before appending new data.
    """
    suppliers_path = (
        Path(suppliers_dir)
        if suppliers_dir is not None
        else Path(history_file).parent
    )
    sup_map = _load_supplier_map(suppliers_path)

    df["supplier_name"] = df["sifra_dobavitelja"].apply(
        lambda x: sup_map.get(str(x), {}).get("ime", str(x))
    )
    primary_code = main_supplier_code(df)
    info = sup_map.get(primary_code, {})
    primary_name = (
        df[df["sifra_dobavitelja"] == primary_code]["supplier_name"].iloc[0]
        if primary_code
        else df["supplier_name"].iloc[0]
    )
    vat_id = info.get("vat") if isinstance(info, dict) else None
    if not vat_id:
        folder_name = Path(history_file).parent.name
        if folder_name.startswith("SI") and folder_name[2:].isdigit():
            vat_id = folder_name

    safe_id = sanitize_folder_name(vat_id or primary_name)
    if safe_id == "unknown" and vat_id:
        safe_id = sanitize_folder_name(vat_id)

    history_path = suppliers_path / safe_id / "price_history.xlsx"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    # Ustvari ključ iz sifra_dobavitelja in naziv
    df_hist = df[
        [
            "sifra_dobavitelja",
            "naziv",
            "cena_netto",
            "total_net",
            "kolicina_norm",
            "enota_norm",
        ]
    ].copy()
    df_hist["key"] = (
        df_hist["sifra_dobavitelja"].astype(str)
        + "_"
        + df_hist["naziv"].str.replace(r"[^\w\s]", "_", regex=True)
    )
    df_hist.rename(
        columns={
            "sifra_dobavitelja": "code",
            "naziv": "name",
            "cena_netto": "line_netto",
        },
        inplace=True,
    )
    df_hist["unit_price"] = df_hist.apply(
        lambda r: (
            Decimal(str(r["total_net"])) / Decimal(str(r["kolicina_norm"]))
            if r["enota_norm"] in ("kg", "L") and r["kolicina_norm"]
            else pd.NA
        ),
        axis=1,
    )
    df_hist.drop(columns=["total_net"], inplace=True)
    df_hist.drop(columns=["kolicina_norm"], inplace=True)

    # Remove rows where the value that will be graphed is zero.  ``unit_price``
    # is preferred when available; otherwise ``line_netto`` is used.  Entries
    # with a zero price are not useful for plotting and can cause very skewed
    # axis ranges.
    price_col = pd.to_numeric(df_hist["unit_price"], errors="coerce")
    fallback = pd.to_numeric(df_hist["line_netto"], errors="coerce")
    df_hist = df_hist[price_col.fillna(fallback).ne(0)]

    if service_date:
        try:
            dt = pd.to_datetime(service_date)
        except Exception:
            dt = pd.Timestamp.now()
        df_hist["time"] = dt
    else:
        df_hist["time"] = pd.Timestamp.now()
    df_hist["service_date"] = service_date
    df_hist["invoice_id"] = invoice_id

    # Preveri, ali so podatki pravilni
    if df_hist["key"].isna().any() or df_hist["key"].str.strip().eq("").any():
        log.warning(
            "Nekateri ključi v zgodovini cen so prazni ali neveljavni!"
        )
    log.debug(f"Zgodovina cen: {df_hist.head().to_dict()}")

    if history_path.exists():
        old = pd.read_excel(history_path, dtype={"key": str})
        if "code" not in old.columns or "name" not in old.columns:
            parts = old["key"].str.split("_", n=1, expand=True)
            if "code" not in old.columns:
                old["code"] = parts[0]
            if "name" not in old.columns:
                old["name"] = parts[1].fillna("")
        if "line_netto" not in old.columns and "cena" in old.columns:
            old.rename(columns={"cena": "line_netto"}, inplace=True)
        if "unit_price" not in old.columns:
            old["unit_price"] = pd.NA
        if "enota_norm" not in old.columns:
            old["enota_norm"] = pd.NA
        if "invoice_id" not in old.columns:
            old["invoice_id"] = pd.NA
        if invoice_id is not None:
            mask = (old["invoice_id"] == invoice_id) & (
                old["key"].isin(df_hist["key"])
            )
            old = old[~mask]
        if not old.empty:
            df_hist = pd.concat([old, df_hist], ignore_index=True)

    df_hist = (
        df_hist.sort_values("time", ascending=False)
        .groupby("key", as_index=False)
        .head(max_entries_per_code)
    )
    df_hist = df_hist[
        [
            "key",
            "code",
            "name",
            "line_netto",
            "unit_price",
            "enota_norm",
            "time",
            "service_date",
            "invoice_id",
        ]
    ]
    df_hist.to_excel(history_path, index=False)


def history_contains(invoice_id: str, history_path: Union[str, Path]) -> bool:
    """Return ``True`` if ``price_history.xlsx`` already contains
    ``invoice_id``."""

    if not invoice_id:
        return False

    path = Path(history_path)
    if not path.exists():
        return False

    try:
        hist = pd.read_excel(path, dtype=str)
    except Exception as exc:
        log.warning(f"Napaka pri branju {path}: {exc}")
        return False

    if "invoice_id" not in hist.columns:
        return False

    return hist["invoice_id"].astype(str).eq(str(invoice_id)).any()


def last_price_stats(item_df: pd.DataFrame) -> dict:
    """Return last price statistics for a single article history.

    Parameters
    ----------
    item_df : pandas.DataFrame
        DataFrame with columns ``cena`` and ``time`` representing one
        article's price history sorted by time.

    Returns
    -------
    dict
        Dictionary with keys ``last_price``, ``last_dt``, ``min`` and ``max``.
        Values are :class:`~decimal.Decimal` for prices and
        :class:`pandas.Timestamp` for the date. ``None`` values are returned
        when mandatory columns are missing or the input frame is empty.
    """

    required = {"cena", "time"}
    if not required.issubset(item_df.columns) or item_df.empty:
        return {"last_price": None, "last_dt": None, "min": None, "max": None}

    df = item_df.dropna(subset=["cena", "time"]).copy()
    if df.empty:
        return {"last_price": None, "last_dt": None, "min": None, "max": None}

    df.sort_values("time", inplace=True)
    prices = df["cena"].apply(lambda x: Decimal(str(x)))
    times = pd.to_datetime(df["time"])

    return {
        "last_price": prices.iloc[-1],
        "last_dt": times.iloc[-1],
        "min": prices.min(),
        "max": prices.max(),
    }


def load_last_price(label: str, suppliers_dir: Path) -> Decimal | None:
    """Return the most recent price for ``label`` from all suppliers.

    The function scans all ``price_history.xlsx`` files below ``suppliers_dir``
    and returns the price from the newest entry matching ``label``.  ``label``
    should be in the form ``"<code> - <name>"`` as produced by
    :func:`log_price_history`.
    """

    latest_dt: pd.Timestamp | None = None
    latest_price: Decimal | None = None

    for hist_file in suppliers_dir.glob("*/price_history.xlsx"):
        try:
            df = pd.read_excel(hist_file)
        except Exception as exc:  # pragma: no cover - invalid file
            log.warning("Napaka pri branju %s: %s", hist_file, exc)
            continue

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

        if "time" not in df.columns:
            continue

        df["price"] = (
            df["unit_price"]
            .where(df["unit_price"].notna(), df["line_netto"])
            .infer_objects(copy=False)
        )
        if df["price"].isna().all():
            continue

        df["label"] = df["code"].astype(str) + " - " + df["name"].astype(str)
        sub = df[df["label"] == label].dropna(subset=["price", "time"])
        if sub.empty:
            continue

        sub.sort_values("time", inplace=True)
        row = sub.iloc[-1]
        dt = pd.to_datetime(row["time"])
        price = Decimal(str(row["price"]))
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_price = price

    return latest_price


def average_cost(
    df: pd.DataFrame, *, skip_zero: bool | None = None
) -> Decimal:
    """Return weighted average unit cost from invoice lines.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame with columns ``cena_netto`` and ``kolicina``.
    skip_zero : bool, optional
        When ``True`` lines with zero price are ignored. Defaults to the value
        of the ``AVG_COST_SKIP_ZERO`` environment variable.
    """
    if skip_zero is None:
        env = os.getenv("AVG_COST_SKIP_ZERO", "0")
        skip_zero = env.lower() not in {"0", "false", "no"}

    if (
        df.empty
        or "cena_netto" not in df.columns
        or "kolicina" not in df.columns
    ):
        return Decimal("0")

    total_val = Decimal("0")
    total_qty = Decimal("0")
    for _, row in df.iterrows():
        try:
            price = Decimal(str(row["cena_netto"]))
            qty = Decimal(str(row["kolicina"]))
        except Exception:
            continue
        if skip_zero and price == 0:
            continue
        total_val += price * qty
        total_qty += qty

    if total_qty == 0:
        return Decimal("0")

    avg = total_val / total_qty
    return avg.quantize(Decimal("0.0001"))
