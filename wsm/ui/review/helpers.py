from __future__ import annotations

import logging
import math
import os
import re
from decimal import Decimal
from typing import Tuple

from wsm.constants import WEIGHTS_PER_PIECE

import pandas as pd

log = logging.getLogger(__name__)

# Threshold for price change warnings in percent used by GUI
PRICE_DIFF_THRESHOLD = Decimal(os.getenv("WSM_PRICE_WARN_PCT", "5"))


def _fmt(v) -> str:
    """Human-friendly format števil (Decimal / float / int)."""
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
    r"(?:teža|masa|weight)?\s*[:\s]?\s*([0-9]+[\.,]?[0-9]*)\s*((?:kgm?)|kgr|g|gr|gram|grams|mg|milligram|milligrams)\b",
    re.I,
)
_rx_fraction = re.compile(r"(\d+(?:[.,]\d+)?)/1\b", re.I)


def _dec(x: str) -> Decimal:
    """Convert a comma-separated string to ``Decimal``."""
    return Decimal(x.replace(",", "."))


def _norm_unit(
    q: Decimal,
    u: str,
    name: str,
    vat_rate: Decimal | float | str | None = None,
    code: str | None = None,
) -> Tuple[Decimal, str]:
    """Normalize quantity and unit to (kg / L / ``kos``)."""
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
            f"Enota v unit_map: {u} -> base_unit={base_unit}, factor={factor}, q_norm={q_norm}"
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
            f"Enota ni v unit_map: u_norm={u_norm}, base_unit={base_unit}, q_norm={q_norm}"
        )

    if base_unit == "kos":
        m_weight = re.search(
            r"(?:teža|masa|weight)?\s*[:\s]?\s*(\d+(?:[.,]\d+)?)\s*(mg|g|dag|kg)\b",
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
                f"Teža najdena v imenu: {val} {unit}, pretvorjeno v kg: {weight_kg}"
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
                f"Volumen najden v imenu: {val} {unit}, pretvorjeno v L: {volume_l}"
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
                    f"Teža iz tabele WEIGHTS_PER_PIECE: {code} {clean_name} -> {weight} kg"
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
            log.debug(
                f"Fractional volume detected: {val}/1 -> using {val} L"
            )
            return q_norm * val, "L"
        log.debug("VAT rate 9.5% detected -> using 'kg' as fallback unit")
        base_unit = "kg"

    if base_unit == "kos" and q_norm != q_norm.to_integral_value():
        name_l = name.lower()
        m_frac = _rx_fraction.search(name_l)
        if m_frac:
            val = _dec(m_frac.group(1))
            log.debug(
                f"Fractional volume detected outside VAT 9.5: {val}/1 -> using {val} L"
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
                f"Volume detected for fractional pieces: {val} {typ}, converted to L: {conv}"
            )
            return q_norm * conv, "L"

        log.debug("Fractional piece quantity detected -> using 'kg' as fallback unit")
        return q_norm, "kg"

    log.debug(f"Končna normalizacija: q_norm={q_norm}, base_unit={base_unit}")
    return q_norm, base_unit
