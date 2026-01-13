"""Store and manage "OSTALO" (unmapped) invoice items.

This module provides functionality to:
- Generate stable signatures for invoice items
- Track confirmed "OSTALO" items across sessions
- Detect and mark storno pairs (canceling +/- items)
- Export new unmapped items for review
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from pandas import Series

log = logging.getLogger(__name__)


def _as_dec(val, default="0") -> Decimal:
    """Convert value to Decimal with fallback."""
    if pd.isna(val):
        return Decimal(default)
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


def make_ostalo_sig(df: pd.DataFrame, supplier_code: str) -> Series:
    """Create stable signature for invoice items.

    The signature is used to identify items across sessions without
    depending on price (which may change). Format:
        supplier_code | sifra_dobavitelja | ddv_stopnja | enota

    Args:
        df: DataFrame with invoice items
        supplier_code: Supplier identifier

    Returns:
        Series with signature strings
    """
    # Normalize supplier code
    sup_code = str(supplier_code or "").strip()

    # Get sifra_dobavitelja (supplier's item code)
    sifra = df.get("sifra_dobavitelja", pd.Series("", index=df.index))
    sifra = sifra.fillna("").astype(str).str.strip()

    # Get ddv_stopnja (VAT rate)
    ddv = df.get("ddv_stopnja", pd.Series(0, index=df.index))
    ddv = ddv.apply(lambda x: str(_as_dec(x, "0")))

    # Get enota (unit)
    enota = df.get("enota", df.get("enota_norm", pd.Series("", index=df.index)))
    enota = enota.fillna("").astype(str).str.strip().str.upper()

    # Build signature: supplier|code|vat|unit
    sig = (
        sup_code + "|" +
        sifra + "|" +
        ddv + "|" +
        enota
    )

    # Empty signature for items without supplier code
    sig = sig.where(sifra.ne(""), "")

    return sig


def load_confirmed(path: Path) -> set[str]:
    """Load confirmed OSTALO signatures from CSV.

    Args:
        path: Path to confirmed_ostalo.csv

    Returns:
        Set of signature strings
    """
    if not path.exists():
        log.debug("Confirmed OSTALO file not found: %s", path)
        return set()

    try:
        df = pd.read_csv(path, dtype=str)
        if "sig" not in df.columns:
            log.warning("No 'sig' column in %s", path)
            return set()

        sigs = df["sig"].fillna("").str.strip()
        sigs = sigs[sigs.ne("")]
        result = set(sigs)
        log.info("Loaded %d confirmed OSTALO signatures from %s", len(result), path.name)
        return result
    except Exception as exc:
        log.warning("Failed to load confirmed OSTALO from %s: %s", path, exc)
        return set()


def append_confirmed(df: pd.DataFrame, path: Path) -> None:
    """Append confirmed OSTALO items to CSV.

    Saves all items where status starts with "OSTALO" and _ostalo_sig is not empty.
    Deduplicates to avoid duplicate signatures.

    Args:
        df: DataFrame with invoice items
        path: Path to confirmed_ostalo.csv
    """
    if "_ostalo_sig" not in df.columns:
        log.warning("No _ostalo_sig column, cannot append confirmed OSTALO")
        return

    if "status" not in df.columns:
        log.warning("No status column, cannot append confirmed OSTALO")
        return

    # Filter items with OSTALO status and valid signature
    status = df["status"].astype(str).str.strip()
    sig = df["_ostalo_sig"].astype(str).str.strip()

    ostalo_mask = status.str.startswith("OSTALO") & sig.ne("")

    if not ostalo_mask.any():
        log.debug("No OSTALO items to append")
        return

    new_sigs = sig[ostalo_mask].unique()
    log.info("Appending %d new OSTALO signatures", len(new_sigs))

    # Load existing signatures
    existing = load_confirmed(path)

    # Combine and deduplicate
    all_sigs = existing | set(new_sigs)

    # Write back
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"sig": sorted(all_sigs)}).to_csv(path, index=False)
        log.info("Saved %d confirmed OSTALO signatures to %s", len(all_sigs), path.name)
    except Exception as exc:
        log.error("Failed to save confirmed OSTALO to %s: %s", path, exc)


def export_new(df: pd.DataFrame, mask: Series, path: Path) -> None:
    """Export new OSTALO items to CSV for review.

    Args:
        df: DataFrame with invoice items
        mask: Boolean mask for items to export
        path: Path to output CSV (e.g., ostalo_novo_{supplier}.csv)
    """
    if not mask.any():
        log.debug("No new OSTALO items to export")
        return

    export_df = df[mask].copy()

    # Select relevant columns
    cols = []
    for c in ["sifra_dobavitelja", "naziv", "ddv_stopnja", "enota", "_ostalo_sig"]:
        if c in export_df.columns:
            cols.append(c)

    if not cols:
        log.warning("No columns to export for new OSTALO")
        return

    export_df = export_df[cols]

    # Deduplicate by signature
    if "_ostalo_sig" in export_df.columns:
        export_df = export_df.drop_duplicates(subset=["_ostalo_sig"], keep="first")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        export_df.to_csv(path, index=False)
        log.info("Exported %d new OSTALO items to %s", len(export_df), path.name)
    except Exception as exc:
        log.error("Failed to export new OSTALO to %s: %s", path, exc)


def mark_auto_storno(df: pd.DataFrame, supplier_code: str) -> Series:
    """Mark items that are storno pairs (canceling +/- items).

    A storno pair is identified when:
    - Same cancel_key (stricter than ostalo_sig, includes price)
    - One positive, one negative item
    - Total net amount ≈ 0 (tolerance 0.005)

    Args:
        df: DataFrame with invoice items
        supplier_code: Supplier identifier

    Returns:
        Boolean Series indicating storno items
    """
    # Build stricter key including price
    sup_code = str(supplier_code or "").strip()

    sifra = df.get("sifra_dobavitelja", pd.Series("", index=df.index))
    sifra = sifra.fillna("").astype(str).str.strip()

    ddv = df.get("ddv_stopnja", pd.Series(0, index=df.index))
    ddv = ddv.apply(lambda x: str(_as_dec(x, "0")))

    enota = df.get("enota", df.get("enota_norm", pd.Series("", index=df.index)))
    enota = enota.fillna("").astype(str).str.strip().str.upper()

    # Try to get unit price
    price = None
    for col in ["cena_netto", "unit_price", "cena"]:
        if col in df.columns:
            price = df[col].apply(lambda x: str(_as_dec(x, "0")))
            break

    if price is None:
        price = pd.Series("0", index=df.index)

    # Build cancel_key with price
    cancel_key = (
        sup_code + "|" +
        sifra + "|" +
        ddv + "|" +
        enota + "|" +
        price
    )

    # Get net amount
    net = None
    for col in ["total_net", "Skupna neto", "vrednost"]:
        if col in df.columns:
            net = df[col].apply(_as_dec)
            break

    if net is None:
        log.warning("No net amount column found, cannot detect storno")
        return pd.Series(False, index=df.index)

    # Group by cancel_key and check for storno pairs
    storno_mask = pd.Series(False, index=df.index)

    for key, group in df.groupby(cancel_key):
        if key == "" or len(group) < 2:
            continue

        # Get net amounts for this group
        group_net = net.loc[group.index]
        total = group_net.sum()

        # Check if there are both positive and negative items
        has_pos = (group_net > 0).any()
        has_neg = (group_net < 0).any()

        # Check if they cancel out (tolerance 0.005)
        if has_pos and has_neg and abs(total) <= Decimal("0.005"):
            log.info(
                "Storno pair detected: key=%s, count=%d, total=%s",
                key[:50] + "..." if len(key) > 50 else key,
                len(group),
                total,
            )
            storno_mask.loc[group.index] = True

    log.info("Marked %d items as storno", storno_mask.sum())
    return storno_mask
