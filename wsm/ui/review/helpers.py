from __future__ import annotations

import logging
import math
import os
import re
from decimal import Decimal
from typing import Tuple

from wsm.constants import (
    WEIGHTS_PER_PIECE,
    PRICE_DIFF_THRESHOLD as DEFAULT_PRICE_DIFF_THRESHOLD,
)

import pandas as pd

log = logging.getLogger(__name__)

# Threshold for price change warnings in percent used by GUI
_env_threshold = os.getenv("WSM_PRICE_WARN_PCT")
PRICE_DIFF_THRESHOLD = (
    Decimal(_env_threshold)
    if _env_threshold is not None
    else DEFAULT_PRICE_DIFF_THRESHOLD
)


def _fmt(v) -> str:
    """Return a human-friendly representation of ``v``.

    Args:
        v: Numeric value convertible to :class:`~decimal.Decimal`.

    Returns:
        str: ``v`` formatted without trailing zeros.
    """
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return ""
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    d = d.quantize(Decimal("0.0001"))
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


_piece = {"kos", "kom", "stk", "st", "can", "ea", "pcs"}
_mass = {"kg", "g", "gram", "grams", "mg", "milligram", "milligrams"}
_vol = {"l", "ml", "cl", "dl", "dcl"}
_rx_vol = re.compile(r"([0-9]+[\.,]?[0-9]*)\s*(ml|cl|dl|dcl|l)\b", re.I)
_rx_mass = re.compile(
    r"(?:teža|masa|weight)?\s*[:\s]?\s*([0-9]+[\.,]?[0-9]*)\s*((?:kgm?)|kgr|g|gr|gram|grams|mg|milligram|milligrams)\b",  # noqa: E501
    re.I,
)
_rx_fraction = re.compile(r"(\d+(?:[.,]\d+)?)/1\b", re.I)


def _dec(x: str) -> Decimal:
    """Convert a comma-separated string to :class:`~decimal.Decimal`.

    Args:
        x (str): Numeric value using a comma as decimal separator.

    Returns:
        Decimal: Parsed numeric value.
    """
    return Decimal(x.replace(",", "."))


def _norm_unit(
    q: Decimal,
    u: str,
    name: str,
    vat_rate: Decimal | float | str | None = None,
    code: str | None = None,
) -> Tuple[Decimal, str]:
    """Normalize quantity and unit to ``kg``/``L``/``kos``.

    Parameters
    ----------
    q : Decimal
        Original quantity value.
    u : str
        Unit code or textual unit.
    name : str
        Item description used for unit detection.
    vat_rate : Decimal | float | str | None, optional
        VAT rate used for fallback heuristics.
    code : str | None, optional
        Supplier article code for weight lookup.

    Returns
    -------
    tuple[Decimal, str]
        ``(quantity, unit)`` in normalized form.
    """
    log.debug(f"Normalizacija: q={q}, u={u}, name={name}")
    unit_map = {
        "KGM": ("kg", 1),
        "GRM": ("kg", 0.001),
        "LTR": ("L", 1),
        "MLT": ("L", 0.001),
        "H87": ("kos", 1),
        "EA": ("kos", 1),
    }

    if u in unit_map:
        base_unit, factor = unit_map[u]
        q_norm = q * Decimal(str(factor))
        log.debug(
            f"Enota v unit_map: {u} -> base_unit={base_unit}, factor={factor}, q_norm={q_norm}"  # noqa: E501
        )
    else:
        u_norm = (u or "").strip().lower()
        if u_norm in _piece:
            base_unit = "kos"
            q_norm = q
        elif u_norm in _mass:
            if u_norm.startswith("kg"):
                factor = Decimal("1")
            elif u_norm.startswith("mg") or u_norm.startswith("milligram"):
                factor = Decimal("1") / Decimal("1000000")
            else:
                factor = Decimal("1") / Decimal("1000")
            q_norm = q * factor
            base_unit = "kg"
        elif u_norm in _vol:
            mapping = {"l": 1, "ml": 1e-3, "cl": 1e-2, "dl": 1e-1, "dcl": 1e-1}
            q_norm = q * Decimal(str(mapping[u_norm]))
            base_unit = "L"
        else:
            name_l = name.lower()
            m_vol = _rx_vol.search(name_l)
            if m_vol:
                val, typ = _dec(m_vol[1]), m_vol[2].lower()
                conv = {
                    "ml": val / 1000,
                    "cl": val / 100,
                    "dl": val / 10,
                    "dcl": val / 10,
                    "l": val,
                }[typ]
                q_norm = q * conv
                base_unit = "L"
            else:
                m_mass = _rx_mass.search(name_l)
                if m_mass:
                    val, typ = _dec(m_mass[1]), m_mass[2].lower()
                    if typ.startswith("kg"):
                        conv = val
                    elif typ.startswith("mg") or typ.startswith("milligram"):
                        conv = val / 1000000
                    else:
                        conv = val / 1000
                    q_norm = q * conv
                    base_unit = "kg"
                else:
                    q_norm = q
                    base_unit = "kos"
        log.debug(
            f"Enota ni v unit_map: u_norm={u_norm}, base_unit={base_unit}, q_norm={q_norm}"  # noqa: E501
        )

    if base_unit == "kos":
        m_weight = re.search(
            r"(?:teža|masa|weight)?\s*[:\s]?\s*(\d+(?:[.,]\d+)?)\s*(mg|g|dag|kg)\b",  # noqa: E501
            name,
            re.I,
        )
        if m_weight:
            val = Decimal(m_weight.group(1).replace(",", "."))
            unit = m_weight.group(2).lower()
            if unit == "mg":
                weight_kg = val / 1000000
            elif unit == "g":
                weight_kg = val / 1000
            elif unit == "dag":
                weight_kg = val / 100
            elif unit == "kg":
                weight_kg = val
            log.debug(
                f"Teža najdena v imenu: {val} {unit}, pretvorjeno v kg: {weight_kg}"  # noqa: E501
            )
            return q_norm * weight_kg, "kg"

        m_volume = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", name, re.I)
        if m_volume:
            val = Decimal(m_volume.group(1).replace(",", "."))
            unit = m_volume.group(2).lower()
            if unit == "ml":
                volume_l = val / 1000
            elif unit == "l":
                volume_l = val
            log.debug(
                f"Volumen najden v imenu: {val} {unit}, pretvorjeno v L: {volume_l}"  # noqa: E501
            )

            if volume_l >= 1 or q_norm != q_norm.to_integral_value():
                return q_norm * volume_l, "L"
            else:
                return q_norm, "kos"

        clean_name = re.sub(r"\s+", " ", name.strip().lower())
        if code is not None:
            weight = WEIGHTS_PER_PIECE.get((str(code), clean_name))
            if weight:
                log.debug(
                    f"Teža iz tabele WEIGHTS_PER_PIECE: {code} {clean_name} -> {weight} kg"  # noqa: E501
                )
                return q_norm * weight, "kg"
    try:
        vat = Decimal(str(vat_rate)) if vat_rate is not None else Decimal("0")
    except Exception:
        vat = Decimal("0")

    if base_unit == "kos" and vat == Decimal("9.5"):
        m_frac = _rx_fraction.search(name)
        if m_frac:
            val = _dec(m_frac.group(1))
            log.debug(f"Fractional volume detected: {val}/1 -> using {val} L")
            return q_norm * val, "L"
        log.debug("VAT rate 9.5% detected -> using 'kg' as fallback unit")
        base_unit = "kg"

    if base_unit == "kos" and q_norm != q_norm.to_integral_value():
        name_l = name.lower()
        m_frac = _rx_fraction.search(name_l)
        if m_frac:
            val = _dec(m_frac.group(1))
            log.debug(
                f"Fractional volume detected outside VAT 9.5: {val}/1 -> using {val} L"  # noqa: E501
            )
            return q_norm * val, "L"

        m_vol = _rx_vol.search(name_l)
        if m_vol:
            val, typ = _dec(m_vol[1]), m_vol[2].lower()
            conv = {
                "ml": val / 1000,
                "cl": val / 100,
                "dl": val / 10,
                "dcl": val / 10,
                "l": val,
            }[typ]
            log.debug(
                f"Volume detected for fractional pieces: {val} {typ}, converted to L: {conv}"  # noqa: E501
            )
            return q_norm * conv, "L"

        log.debug(
            "Fractional piece quantity detected -> using 'kg' as fallback unit"
        )
        return q_norm, "kg"

    log.debug(f"Končna normalizacija: q_norm={q_norm}, base_unit={base_unit}")
    return q_norm, base_unit


def _merge_same_items(df: pd.DataFrame) -> pd.DataFrame:
    """Merge identical rows while keeping ``is_gratis`` lines separate.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame with invoice lines. Must contain the ``is_gratis`` column.

    Returns
    -------
    pandas.DataFrame
        New DataFrame where duplicate rows (excluding ``is_gratis``) are
        combined by summing numeric columns. Rows marked with ``is_gratis`` are
        left untouched.
    """

    if "is_gratis" not in df.columns:
        return df

    gratis = df[df["is_gratis"]].copy()
    to_merge = df[~df["is_gratis"]].copy()

    if to_merge.empty:
        return df

    numeric_cols = {
        "kolicina",
        "kolicina_norm",
        "vrednost",
        "rabata",
        "total_net",
    }
    existing_numeric = [c for c in numeric_cols if c in to_merge.columns]
    group_cols = [c for c in to_merge.columns if c not in existing_numeric]

    merged = (
        to_merge.groupby(group_cols, dropna=False)
        .agg({c: "sum" for c in existing_numeric})
        .reset_index()
    )

    return pd.concat([merged, gratis], ignore_index=True)


def _split_totals(
    df: pd.DataFrame,
    doc_discount_total: Decimal | float | int = 0,
    vat_rate: Decimal | float | int = Decimal("0.095"),
) -> tuple[Decimal, Decimal, Decimal]:
    """Return net, VAT and gross totals for review tables.

    Parameters
    ----------
    df : pandas.DataFrame
        Invoice lines excluding document discount rows. Must contain
        ``total_net`` and ``wsm_sifra`` columns and may include ``is_gratis``.
    doc_discount_total : Decimal
        Document level discount amount to apply.
    vat_rate : Decimal | float | int
        VAT rate as a fraction (e.g. ``0.22`` for 22 %). Defaults to ``0.095``.

    Returns
    -------
    tuple[Decimal, Decimal, Decimal]
        ``(net, vat, gross)`` where ``net`` is the sum of all invoice lines
        after discounts.
    """

    valid = df.copy()
    if "deleted" in valid.columns:
        valid = valid[~valid["deleted"].fillna(False)]
    if "is_gratis" in valid.columns:
        valid = valid[~valid["is_gratis"].fillna(False)]

    try:
        dd_total = Decimal(str(doc_discount_total or "0"))
    except Exception:
        dd_total = Decimal("0")

    if "total_net" in valid.columns:
        value_col = "total_net"
    elif "vrednost_postavke" in valid.columns:
        value_col = "vrednost_postavke"
    elif "vrednost" in valid.columns:
        value_col = "vrednost"
    else:
        value_col = None

    if value_col is None:
        return Decimal("0"), Decimal("0"), Decimal("0")

    valid[value_col] = valid[value_col].fillna(0)

    linked_mask = valid["wsm_sifra"].notna() & (
        valid["wsm_sifra"].astype(str).str.strip() != ""
    )
    linked_total = valid.loc[linked_mask, value_col].sum()
    unlinked_total = valid.loc[~linked_mask, value_col].sum()

    if dd_total:
        if linked_total:
            linked_total += dd_total
        else:
            unlinked_total += dd_total

    net_amount = linked_total + unlinked_total

    try:
        rate = Decimal(str(vat_rate))
    except Exception:
        rate = Decimal("0")

    vat = (
        (net_amount * rate).quantize(Decimal("0.01"))
        if net_amount
        else Decimal("0")
    )
    gross = (net_amount + vat).quantize(Decimal("0.01"))

    return net_amount, vat, gross


def _apply_price_warning(
    new_price: Decimal | float | int,
    prev_price: Decimal | None,
    *,
    threshold: Decimal = PRICE_DIFF_THRESHOLD,
) -> tuple[bool, str | None]:
    """Return price warning info for ``new_price`` compared to ``prev_price``.

    Parameters
    ----------
    new_price : Decimal | float | int
        Latest unit price.
    prev_price : Decimal | None
        Previous unit price or ``None`` when unavailable.
    threshold : Decimal, optional
        Warning threshold as percent difference.

    Returns
    -------
    tuple[bool, str | None]
        ``(warn, tooltip)`` where ``warn`` indicates whether the price change
        exceeds ``threshold`` and ``tooltip`` is the text for GUI display. When
        ``prev_price`` is ``None`` the tooltip is ``None`` and ``warn`` is
        ``False``.
    """
    if prev_price is None or prev_price == 0:
        return False, None

    new_val = Decimal(str(new_price))
    diff = (new_val - prev_price).quantize(Decimal("0.01"))
    if abs(diff) <= Decimal("0.02"):
        return False, ""

    diff_pct = ((new_val - prev_price) / prev_price * Decimal("100")).quantize(
        Decimal("0.01")
    )

    if abs(diff_pct) > threshold:
        return True, f"±{diff:.2f} €"

    return False, ""
