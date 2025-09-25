from __future__ import annotations

import logging
import re
import math
import os
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Sequence, Tuple

from wsm.constants import (
    WEIGHTS_PER_PIECE,
    PRICE_DIFF_THRESHOLD as DEFAULT_PRICE_DIFF_THRESHOLD,
)

import numpy as np  # required for np.bool_ in _fmt
import pandas as pd

DEC2 = Decimal("0.01")
GROUP_BY_DISCOUNT = os.getenv("WSM_GROUP_BY_DISCOUNT", "1") not in {
    "0",
    "false",
    "False",
}
# Združuj bolj "ohlapno": če je vključen, ignoriraj supplier kodo,
# ko obstaja WSM šifra (zliva po wsm_sifra + naziv_ckey +
# enota_norm + is_gratis).
RELAXED_MERGE = os.getenv("WSM_RELAXED_MERGE", "0") not in {
    "0",
    "false",
    "False",
}


def q2(x: Decimal) -> Decimal:
    return x.quantize(DEC2, rounding=ROUND_HALF_UP)


def to_dec(x) -> Decimal:
    try:
        if isinstance(x, Decimal):
            return x
        if pd.isna(x):
            return Decimal("0")
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def series_to_dec(s: pd.Series) -> pd.Series:
    return s.map(to_dec)


# --- robust Decimal coercion (similar to GUI helper) ---
def _as_dec(x, default: str = "0") -> Decimal:
    """Convert value to Decimal safely.

    Any NaN/None/empty/invalid → Decimal(default).
    Also normalizes comma decimals.
    """
    try:
        if isinstance(x, Decimal):
            return x if x.is_finite() else Decimal(default)
        if pd.isna(x) or x in ("", None):
            return Decimal(default)
        s = str(x).strip().replace(",", ".")
        if not s:
            return Decimal(default)
        d = Decimal(s)
        return d if d.is_finite() else Decimal(default)
    except Exception:
        return Decimal(default)


log = logging.getLogger(__name__)
_TRACE = os.getenv("WSM_TRACE", "0") not in {"0", "false", "False"}
_LOG = logging.getLogger(__name__)


def _t(msg, *args):
    if _TRACE:
        _LOG.warning("[TRACE MERGE] " + msg, *args)


# Threshold for price change warnings in percent used by GUI
_env_threshold = os.getenv("WSM_PRICE_WARN_PCT")
PRICE_DIFF_THRESHOLD = (
    Decimal(_env_threshold)
    if _env_threshold is not None
    else DEFAULT_PRICE_DIFF_THRESHOLD
)


NET_CANDIDATES = [
    "Neto po rabatu",
    "vrednost",
    "Skupna neto",
    "vrednost_po_rabatu",
    "total_net",
    "net_line",
    "neto",
    "cena_po_rabatu",
]

DISC_CANDIDATES = [
    "rabata",
    "rabat",
    "discount_amount",
    "rabat_znesek",
    "znesek_rabata",
    "moa204",
]

GROSS_CANDIDATES = [
    "Bruto",
    "vrednost_bruto",
    "bruto_line",
    "Skupna bruto",
    "cena_bruto",
]


def _fmt(v) -> str:
    """Return a human-friendly representation of ``v``.

    Args:
        v: Numeric value convertible to :class:`~decimal.Decimal`.

    Returns:
        str: ``v`` formatted without trailing zeros.
    """
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, pd.Series):
        if v.empty or pd.isna(v).all():
            return ""
        v = v.iloc[0]
    elif pd.isna(v):
        return ""
    if isinstance(v, (bool, np.bool_)):
        v = int(v)
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    d = d.quantize(Decimal("0.0001"))
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _norm_wsm_code(code) -> str:
    """
    Normalizira WSM šifro za grupiranje/prikaz:
      • None/NaN -> "" (prazno)
      • odreži presledke
      • '100100.0' -> '100100' (če je videti kot celo število z .0)
      • '0', '0,0', '000' … -> "" (vse ničelne variante štejejo kot nekodirano)
    """
    if code is None:
        return ""
    try:
        if pd.isna(code):
            return ""
    except Exception:
        pass
    s = str(code).strip().replace(",", ".")
    if not s:
        return ""
    # Treat any "0" variant ("0", "0.0", "0,0", "000") as uncoded
    lower = s.lower()
    if lower in {"nan", "none", "null"}:
        return ""
    if re.fullmatch(r"0+(?:\.0+)?", s):
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", s):
        s = s.split(".")[0]
    return s


def _first_scalar(v):
    """Return the first scalar value when ``v`` may be a Series."""

    if isinstance(v, pd.Series):
        v = v.dropna()
        v = v.iloc[0] if not v.empty else None
    return v


def _safe_set_block(
    df: pd.DataFrame,
    cols: Sequence[str],
    data,
) -> pd.DataFrame:
    """Safely assign ``data`` to ``df`` columns ``cols``.

    Parameters
    ----------
    df : pandas.DataFrame
        Target DataFrame to modify in-place.
    cols : sequence[str]
        Column names to populate.
    data : DataFrame | sequence | scalar
        Source data. When a sequence is provided each element is
        reindexed to ``df.index`` and stacked using
        :func:`numpy.column_stack`.

    Returns
    -------
    pandas.DataFrame
        The modified DataFrame. When shapes mismatch the requested
        columns are created and filled with zeros.
    """

    if df.empty:
        df.loc[:, cols] = 0
        return df

    try:
        if np.isscalar(data):
            block = np.full((len(df), len(cols)), data)
        elif isinstance(data, pd.DataFrame):
            block = data.reindex(df.index).fillna(0).to_numpy()
        else:
            if not isinstance(data, Sequence):
                data = [data]
            block = np.column_stack(
                [
                    pd.Series(d).reindex(df.index).fillna(0).to_numpy()
                    for d in data
                ]
            )

        if block.shape != (len(df), len(cols)):
            raise ValueError("Shape mismatch")
        df.loc[:, cols] = block
    except Exception:
        # Fallback: align by index and fill numeric columns with 0,
        # textual columns with an empty string.
        for c in cols:
            col_series = df.get(c)
            num = (
                pd.to_numeric(col_series, errors="coerce")
                if col_series is not None
                else None
            )
            if num is not None and num.notna().any():
                df[c] = num.reindex(df.index).fillna(0)
            else:
                s = (
                    col_series
                    if col_series is not None
                    else pd.Series(index=df.index, dtype=object)
                )
                df[c] = s.reindex(df.index).fillna("")

    return df


from wsm.ui.review import summary_utils  # noqa: E402

summary_df_from_records = summary_utils.summary_df_from_records


_piece = {"kos", "kom", "stk", "st", "can", "ea", "pcs"}
_mass = {"kg", "g", "gram", "grams", "mg", "milligram", "milligrams"}
_vol = {"l", "ml", "cl", "dl", "dcl"}
_rx_vol = re.compile(r"([0-9]+[\.,]?[0-9]*)\s*(ml|cl|dl|dcl|l)\b", re.I)
_rx_mass = re.compile(
    r"(?:teža|masa|weight)?\s*[:\s]?\s*([0-9]+[\.,]?[0-9]*)\s*((?:kgm?)|kgr|g|gr|gram|grams|mg|milligram|milligrams)\b",  # noqa: E501
    re.I,
)
_rx_fraction = re.compile(r"(\d+(?:[.,]\d+)?)/1\b", re.I)
_piece_hint_words = (
    "kos",
    "kosov",
    "kom",
    "komad",
    "komada",
    "pcs",
    "piece",
    "pieces",
    "pc",
)
_rx_piece_hint = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in _piece_hint_words) + r")\b",
    re.I,
)


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
    override_unit: str | None = None,
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
    name = name or ""
    name_l = name.lower()
    unit_from_map = False
    unit_from_piece_token = False
    has_piece_hint = bool(_rx_piece_hint.search(name_l))
    unit_map = {
        "KGM": ("kg", 1),
        "GRM": ("kg", 0.001),
        "LTR": ("L", 1),
        "MLT": ("L", 0.001),
        "H87": ("kos", 1),
        "EA": ("kos", 1),
    }

    if u in unit_map:
        unit_from_map = True
        base_unit, factor = unit_map[u]
        q_norm = q * Decimal(str(factor))
        if base_unit == "kos":
            has_piece_hint = True
        log.debug(
            f"Enota v unit_map: {u} -> base_unit={base_unit}, factor={factor}, q_norm={q_norm}"  # noqa: E501
        )
    else:
        u_norm = (u or "").strip().lower()
        if u_norm in _piece:
            base_unit = "kos"
            q_norm = q
            unit_from_piece_token = True
            has_piece_hint = True
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
        # Zaznaj maso v nazivu, npr. '100g', '0.18kg', '5mg'
        m_mass = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(mg|g|kg)\b", name_l)
        if has_piece_hint and m_mass:
            # Izvleci številko in enoto
            num = m_mass.group(1).replace(",", ".")
            unit = m_mass.group(2).lower()
            val = Decimal(num)
            # Pretvori v kg
            if unit == "mg":
                mass_kg = val / Decimal("1000000")
            elif unit == "g":
                mass_kg = val / Decimal("1000")
            else:  # kg
                mass_kg = val
            log.debug(
                f"Kosovni artikel z maso: {q_norm} kos × {mass_kg} kg = {q_norm * mass_kg} kg"
            )
            return q_norm * mass_kg, "kg"

        # Če ni mase, ohrani 'kos' za eksplicitne kosovne artikle
        if unit_from_map or unit_from_piece_token or has_piece_hint:
            log.debug("VAT 9.5% + kos brez mase -> keeping 'kos'")
        else:
            log.debug("VAT 9.5% brez kosovnih namigov -> fallback to 'kg'")
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

    override_clean = None
    if override_unit is not None:
        try:
            if pd.isna(override_unit):
                override_clean = None
            else:
                override_clean = str(override_unit).strip()
        except Exception:
            override_clean = str(override_unit).strip()
        if override_clean:
            override_lower = override_clean.lower()
            if override_lower == "l":
                log.debug("Override unit applied: using raw quantity for liters")
                return q, "L"
            if override_lower == "kos":
                log.debug("Override unit applied: using raw quantity for pieces")
                return q, "kos"
            if override_lower == "kg":
                log.debug("Override unit applied: using normalized quantity for kilograms")
                return q_norm, "kg"

    log.debug(f"Končna normalizacija: q_norm={q_norm}, base_unit={base_unit}")
    return q_norm, base_unit


def _merge_same_items(df: pd.DataFrame) -> pd.DataFrame:
    """Merge identical rows (tudi GRATIS med sabo), ločeno po rabatnem bucketu.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame with invoice lines. Must contain the ``is_gratis`` column.

    Returns
    -------
    pandas.DataFrame
        New DataFrame where duplicate rows are combined by summing numeric
        columns while keeping GRATIS lines separate from paid ones.
    """
    if "is_gratis" not in df.columns:
        return df

    # Numerični stolpci, ki se naj seštevajo (nikoli del ključa)
    num_candidates = [
        "Količina",
        "kolicina",
        "kolicina_norm",
        "vrednost",
        "rabata",
        "Neto po rabatu",
        "total_net",
        "ddv",
    ]
    existing_numeric = [c for c in num_candidates if c in df.columns]
    _t("start rows=%d numeric=%s", len(df), existing_numeric)

    # Track returns separately so that quantity going back to supplier is not
    # lost when positive and negative rows cancel each other out during
    # aggregation. ``vrnjeno`` stores the absolute value of negative quantities
    # and is summed like other numeric columns.
    if "kolicina_norm" in df.columns and "vrnjeno" not in df.columns:
        df["vrnjeno"] = df["kolicina_norm"].map(
            lambda x: -to_dec(x) if to_dec(x) < 0 else Decimal("0")
        )
        existing_numeric.append("vrnjeno")


    # ➊ Minimalni identitetni ključ
    base_keys = [
        k
        for k in (
            "sifra_dobavitelja",
            "naziv_ckey",
            "enota_norm",
            "wsm_sifra",
            "is_gratis",
        )
        if k in df.columns
    ]
    # Če želimo zlivati po WSM šifri (in ignorirati različne supplier kode in
    # različne canonical nazive), skrčimo ključ na
    # wsm_sifra + enota_norm + is_gratis.
    if RELAXED_MERGE and "wsm_sifra" in base_keys:
        base_keys = [
            k
            for k in base_keys
            if k not in ("sifra_dobavitelja", "naziv_ckey")
        ]
    # ➋ Ključ rabata za varno združevanje (brez vključevanja cene)
    bucket_keys = []
    # ➌ Končni ključ = identitetni + bucket/rabat (brez “šuma”)
    noise = {
        "naziv",
        "enota",
        "warning",
        "status",
        "dobavitelj",
        "wsm_naziv",
        "cena_bruto",
        "cena_netto",
        "cena_pred_rabatom",
        "rabata_pct",
        "sifra_artikla",
        "ean",
        "ddv_stopnja",
        "multiplier",
    }
    group_cols = [
        c
        for c in list(dict.fromkeys(base_keys + bucket_keys))
        if c not in noise
    ]
    # ohrani dimenzijo rabata v merge ključu, če je to zahtevano
    if globals().get("GROUP_BY_DISCOUNT", True):
        for _dc in ("rabata_pct", "eff_discount_pct"):
            if _dc in df.columns and _dc not in group_cols:
                group_cols.append(_dc)

    log.warning("[TRACE MERGE] MERGE group_cols(final)=%r", group_cols)
    used_group_price = globals().get("GROUP_BY_DISCOUNT", True) and any(
        k in group_cols
        for k in ("_price_key", "_discount_bucket", "line_bucket")
    )

    if not group_cols:
        return df

    df = df.copy()
    # mehka normalizacija za varnost (ne spremeni količin/€):
    if "enota_norm" in df.columns:
        df["enota_norm"] = df["enota_norm"].astype(str).str.strip().str.lower()
    if "naziv_ckey" in df.columns:
        df["naziv_ckey"] = (
            df["naziv_ckey"]
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )
    if "wsm_sifra" in df.columns:
        df["wsm_sifra"] = df["wsm_sifra"].astype(str).str.strip()
    if "_discount_bucket" in df.columns:
        df["_discount_bucket"] = df["_discount_bucket"].astype(object)

    df[existing_numeric] = df[existing_numeric].fillna(Decimal("0"))
    df["_first_idx"] = df.index

    # seštej samo numeriko; prikazne stolpce ohrani kot 'first'
    agg_dict = {c: "sum" for c in existing_numeric}
    for keep in ("naziv", "naziv_ckey", "enota", "warning", "rabata_pct"):
        if keep in df.columns and keep not in group_cols:
            agg_dict[keep] = "first"
    if "cena_po_rabatu" in df.columns and "cena_po_rabatu" not in agg_dict:
        agg_dict["cena_po_rabatu"] = "first"
    if "_booked_sifra" in df.columns and "_booked_sifra" not in group_cols:
        agg_dict["_booked_sifra"] = "first"
    agg_dict["_first_idx"] = "min"

    merged = df.groupby(group_cols, dropna=False).agg(agg_dict).reset_index()

    # --- DIAGNOSTIKA: pokaži skupine, ki so ostale podvojene po
    # osnovnem ključu ---
    try:
        # uporabi dejanski base_keys po morebitnem RELAXED_MERGE
        base_probe = [c for c in base_keys if c in merged.columns]
        if base_probe:
            dups = merged.groupby(
                base_probe, dropna=False, as_index=False
            ).size()
            dups = dups[dups["size"] > 1].head(20)  # prvih 20
            if len(dups):
                _t(
                    "PODVOJENE SKUPINE (brez bucketov)=%s",
                    dups.to_dict("records"),
                )
    except Exception as _exc:
        _t("diag merge duplicates failed: %s", _exc)

    # Poravnava cene za prikaz:
    # - če grupiramo po ceni -> vzemi ceno iz bucket-a
    # - sicer -> uporabi tehtano povprečje total/qty in popravi tudi bucket
    if used_group_price:
        if "_discount_bucket" in merged.columns:
            merged["cena_po_rabatu"] = merged.apply(
                lambda r: (
                    _as_dec(r["_discount_bucket"][1], "0")
                    if (
                        isinstance(r.get("_discount_bucket"), (tuple, list))
                        and len(r["_discount_bucket"]) == 2
                    )
                    else _as_dec(r.get("cena_po_rabatu", "0"), "0")
                ),
                axis=1,
            )
        elif "line_bucket" in merged.columns:
            merged["cena_po_rabatu"] = merged.apply(
                lambda r: (
                    _as_dec(r["line_bucket"][1], "0")
                    if (
                        isinstance(r.get("line_bucket"), (tuple, list))
                        and len(r["line_bucket"]) == 2
                    )
                    else _as_dec(r.get("cena_po_rabatu", "0"), "0")
                ),
                axis=1,
            )
        elif "_price_key" in merged.columns:
            merged["cena_po_rabatu"] = merged["_price_key"].map(
                lambda v: _as_dec(v, "0").quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )
            )

        if "_discount_bucket" not in merged.columns:
            merged["_discount_bucket"] = merged.apply(
                lambda r: (
                    _as_dec(r.get("rabata_pct", "0"), "0").quantize(
                        DEC2, rounding=ROUND_HALF_UP
                    ),
                    _as_dec(r.get("cena_po_rabatu", "0"), "0").quantize(
                        Decimal("0.001"), rounding=ROUND_HALF_UP
                    ),
                ),
                axis=1,
            )
            merged["_discount_bucket"] = merged["_discount_bucket"].astype(
                object
            )
    else:
        qty_col = next(
            (
                c
                for c in ("kolicina_norm", "Količina", "kolicina")
                if c in merged.columns
            ),
            None,
        )
        tot_col = next(
            (
                c
                for c in (
                    "total_net",
                    "Neto po rabatu",
                    "vrednost",
                    "Skupna neto",
                )
                if c in merged.columns
            ),
            None,
        )
        if qty_col and tot_col:

            def _avg_unit(r):
                q = _as_dec(r.get(qty_col), "0")
                t = _as_dec(r.get(tot_col), "0")
                return (t / q) if q else Decimal("0")

            merged["cena_po_rabatu"] = merged.apply(_avg_unit, axis=1).map(
                lambda d: d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            )

        merged["_discount_bucket"] = merged.apply(
            lambda r: (
                _as_dec(r.get("rabata_pct", "0"), "0").quantize(
                    DEC2, rounding=ROUND_HALF_UP
                ),
                _as_dec(r.get("cena_po_rabatu", "0"), "0").quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                ),
            ),
            axis=1,
        )
        merged["_discount_bucket"] = merged["_discount_bucket"].astype(object)

    # eksplicitno nastavi is_gratis:
    # plačljive → False, gratis → True (že v ključu)
    merged["is_gratis"] = merged["is_gratis"].fillna(False).astype(bool)

    try:
        base_cols = [
            c
            for c in ("sifra_dobavitelja", "naziv_ckey", "enota_norm")
            if c in df.columns
        ]
        base = df[base_cols].drop_duplicates().shape[0] if base_cols else "n/a"
        buckets = (
            df["_discount_bucket"].nunique(dropna=False)
            if "_discount_bucket" in df.columns
            else "n/a"
        )
        _t(
            "merged: before=%d, after=%d, distinct base=%s, uniq buckets=%s",
            len(df),
            len(merged),
            base,
            buckets,
        )
    except Exception:
        pass

    # ohrani približen prvotni vrstni red
    return merged.sort_values("_first_idx", kind="stable").drop(
        columns="_first_idx"
    )


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
        (net_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if net_amount
        else Decimal("0")
    )
    gross = (net_amount + vat).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

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


GRATIS_THRESHOLD = Decimal("99.5")


def first_existing(
    df: pd.DataFrame, columns: Sequence[str], fill_value=0
) -> pd.Series:
    """Return the first available column from ``df``.

    Parameters
    ----------
    df : pandas.DataFrame
        Source table.
    columns : sequence[str]
        Candidate column names ordered by preference.
    fill_value : Any, optional
        Value used when no candidate column exists. Missing values within
        the chosen column are also replaced by this value. Defaults to ``0``.

    Returns
    -------
    pandas.Series
        Series taken from the first existing column with missing values
        replaced by ``fill_value``. When none of the columns exist a
        new :class:`~pandas.Series` filled with ``fill_value`` is returned.
    """

    for col in columns:
        if col in df:
            series = df[col]
            if hasattr(series, "ndim") and series.ndim == 2:
                series = series.iloc[:, 0]
            return series.fillna(fill_value)

    # No column found – return a default series
    return pd.Series(fill_value, index=df.index)


def first_existing_series(
    df: pd.DataFrame, columns: Sequence[str], fill_value=0
) -> pd.Series:
    """Return the first existing Series from ``columns``.

    Parameters
    ----------
    df:
        Source table.
    columns:
        Candidate column names ordered by preference.

    Returns
    -------
    pandas.Series
        The series from the first available column or an empty
        series of ``pd.NA`` when none exist.
    """

    return first_existing(df, columns, fill_value=fill_value)


def compute_eff_discount_pct_from_df(
    df: pd.DataFrame,
    pct_candidates: Sequence[str],
    value_candidates: Sequence[str],
    amt_candidates: Sequence[str],
) -> pd.Series:
    """Return effective discount percentages for rows in ``df``.

    ``pct_candidates`` lists columns that may already contain the percentage.
    When none are present, the percentage is derived from the discount and
    value amounts using ``100 * rabat / (rabat + vrednost)``. The result is
    normalised to :class:`~decimal.Decimal` with two decimals; negative values
    are clamped to ``0`` and values of ``99.5`` or more are rounded up to
    ``100``.
    """

    pct_series = None
    for col in pct_candidates:
        if col in df.columns:
            pct_series = pd.to_numeric(df[col], errors="coerce")
            break

    if pct_series is None:
        val = pd.to_numeric(
            first_existing(df, value_candidates), errors="coerce"
        ).fillna(0)
        disc = pd.to_numeric(
            first_existing(df, amt_candidates), errors="coerce"
        ).fillna(0)
        denom = val + disc
        pct_series = pd.Series(
            np.where(denom > 0, (disc / denom) * 100.0, 0.0), index=df.index
        )

    pct_series = pct_series.fillna(0.0)
    pct_series[pct_series < 0] = 0.0
    pct_series[pct_series >= float(GRATIS_THRESHOLD)] = 100.0
    pct_series = pct_series.round(2)


def compute_eff_discount_pct(
    data: pd.DataFrame | pd.Series,
) -> pd.Series | Decimal:
    """Effective discount percentage per row.

    The helper previously operated on an entire ``DataFrame`` and returned a
    ``Series``.  Some call sites now pass a single row (``Series``) – for
    example when using ``DataFrame.apply``.  To keep backwards compatibility we
    accept either input: a DataFrame yields a ``Series`` while a single row
    returns a ``Decimal``.
    """
    # Normalise input to a DataFrame for unified processing
    is_series = isinstance(data, pd.Series)
    df = data.to_frame().T if is_series else data

    disc = pd.to_numeric(
        df.get("rabata", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    )
    net = pd.to_numeric(
        df.get("vrednost", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    )
    denom = net.fillna(0) + disc.fillna(0)
    pct = pd.Series(
        np.where(denom > 0, (disc.fillna(0) / denom) * 100.0, np.nan),
        index=df.index,
    )
    for name in ("Rabat (%)", "rabat", "rabat_pct"):
        if name in df.columns:
            pct = pct.fillna(pd.to_numeric(df[name], errors="coerce"))
            break
    pct = pct.fillna(0.0)
    pct[pct >= 99.5 - 1e-9] = 100.0
    pct = pct.round(2)

    def _to_dec(x: float) -> Decimal:
        try:
            return Decimal(str(x)).quantize(
                Decimal("0.00"), rounding=ROUND_HALF_UP
            )
        except Exception:
            return Decimal("0.00")

    pct = pct.apply(_to_dec)
    return pct.iloc[0] if is_series else pct


def _to_dec(x):
    if x is None:
        return None
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _q2(x: Decimal | None) -> Decimal | None:
    if x is None:
        return None
    return x.quantize(DEC2, rounding=ROUND_HALF_UP)


def compute_eff_discount_pct_robust(df: pd.DataFrame) -> pd.Series:
    pct = None
    # 1) najprej poskusi že podane odstotke (dodaj še rabata_pct, rabat %)
    for c in [
        "eff_discount_pct",
        "Rabat (%)",
        "rabat %",
        "rabat_pct",
        "rabata_pct",
        "discount_pct",
        "line_pct_discount",
    ]:
        if c in df.columns:
            pct = df[c].map(_to_dec).map(_q2)
            break
    if pct is None:
        # 2) kandidati za neto, rabat in bruto
        net = None
        for c in NET_CANDIDATES:
            if c in df.columns:
                net = df[c].map(_to_dec)
                break
        # 3) znesek rabata
        disc = None
        for c in DISC_CANDIDATES:
            if c in df.columns:
                disc = df[c].map(_to_dec)
                break
        gross = None
        for c in GROSS_CANDIDATES:
            if c in df.columns:
                gross = df[c].map(_to_dec)
                break
        # ➊ če imamo bruto in neto → (gross - net) / gross
        if gross is not None and net is not None:
            pct = pd.Series(
                [
                    (
                        None
                        if (g is None or g == 0 or n is None)
                        else ((g - n) * Decimal(100)) / g
                    )
                    for g, n in zip(gross, net)
                ],
                index=df.index,
                dtype=object,
            )
        # ➋ sicer probaj net + rabat → rabat / (net + rabat)
        elif net is not None and disc is not None:
            pct = pd.Series(
                [
                    (
                        None
                        if (
                            (n is None and d is None)
                            or (((n or Decimal(0)) + (d or Decimal(0))) == 0)
                        )
                        else (d * Decimal(100))
                        / ((n or Decimal(0)) + (d or Decimal(0)))
                    )
                    for n, d in zip(net, disc)
                ],
                index=df.index,
                dtype=object,
            )
        # ➌ ali gross + rabat → rabat / gross
        elif gross is not None and disc is not None:
            pct = pd.Series(
                [
                    (
                        None
                        if (g is None or g == 0 or d is None)
                        else (d * Decimal(100)) / g
                    )
                    for g, d in zip(gross, disc)
                ],
                index=df.index,
                dtype=object,
            )
        else:
            pct = pd.Series([None] * len(df), index=df.index, dtype=object)
        pct = pct.map(_q2)

    def _norm(p):
        try:
            d = _to_dec(p)
        except Exception:
            return Decimal("0.00")
        if d is None:
            return Decimal("0.00")
        try:
            if hasattr(d, "is_nan") and d.is_nan():
                return Decimal("0.00")
        except Exception:
            return Decimal("0.00")
        try:
            if d < 0:
                return Decimal("0.00")
            if d >= Decimal("99.5"):
                return Decimal("100.00")
            if d > 100:
                return Decimal("100.00")
        except Exception:
            return Decimal("0.00")
        return _q2(d)

    return pct.map(_norm).astype(object)


def ensure_eff_discount_col(
    df: pd.DataFrame, col_name: str = "eff_discount_pct"
) -> pd.DataFrame:
    eff = compute_eff_discount_pct_robust(df)
    df[col_name] = eff.astype(object)
    return df
