from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import unicodedata
import re

import pandas as pd

__all__ = ["load_catalog", "load_keywords_map"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_key(value: str) -> str:
    """Return a simplified key used for header matching.

    The function lowercases ``value``, removes whitespace, diacritics and
    any non-alphanumeric characters so that a variety of header styles can be
    matched.  ``None`` or non-string inputs return an empty string.
    """

    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_diacritics = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    )
    return re.sub(r"[^0-9a-z]+", "", without_diacritics.lower())


def _to_number(value: Any) -> Any:
    """Convert European-style numeric strings to ``int``/``float``.

    Strings using comma as the decimal separator are converted to use a dot.
    Thousand separators (``.``) are stripped.  Non-convertible values return
    ``pd.NA``.
    """

    if pd.isna(value):
        return pd.NA
    if isinstance(value, (int, float)):
        return value
    try:
        s = str(value).strip()
        if s == "":
            return pd.NA
        s = s.replace(".", "").replace(",", ".")
        if re.fullmatch(r"[-+]?[0-9]+", s):
            return int(s)
        return float(s)
    except Exception:  # pragma: no cover - defensive
        return pd.NA


def _build_alias_map(aliases: Dict[str, set[str]]) -> Dict[str, str]:
    """Return mapping of normalized alias -> canonical name."""

    mapping: Dict[str, str] = {}
    for canonical, names in aliases.items():
        for name in {canonical, *names}:
            mapping[_norm_key(name)] = canonical
    return mapping


# Aliases for catalog headers
CATALOG_ALIASES = {
    "wsm_sifra": {"wsm sifra", "šifra", "sifra", "code"},
    "wsm_naziv": {"wsm naziv", "naziv", "name", "opis"},
    "ean": {"ean", "ean13", "barcode", "bar koda"},
    "pakiranje": {"pakiranje", "pak", "pack", "pakir"},
    "min_kolicina": {
        "min kolicina",
        "minimalna kolicina",
        "minkolicina",
        "min qty",
    },
    "cena": {"cena", "price", "neto", "unit price"},
}
CATALOG_ALIAS_MAP = _build_alias_map(CATALOG_ALIASES)

# Aliases for keyword files
KEYWORD_ALIASES = {
    "wsm_sifra": {"wsm sifra", "šifra", "sifra", "code"},
    "keyword": {"keyword", "kljucna beseda", "kljucnabeseda"},
}
KEYWORD_ALIAS_MAP = _build_alias_map(KEYWORD_ALIASES)


def _rename_with_aliases(
    df: pd.DataFrame, alias_map: Dict[str, str]
) -> pd.DataFrame:
    """Rename ``df`` columns based on ``alias_map``.

    ``alias_map`` should contain normalized column names mapped to canonical
    ones.  Any column not found in the map is left unchanged.
    """

    rename: Dict[str, str] = {}
    for col in df.columns:
        canonical = alias_map.get(_norm_key(col))
        if canonical:
            rename[col] = canonical
    return df.rename(columns=rename)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

NUMERIC_COLS = {"pakiranje", "min_kolicina", "cena"}


def load_catalog(path: str | Path) -> pd.DataFrame:
    """Return normalized catalog data from ``path``.

    Column headers are matched case-insensitively and without diacritics using
    :data:`CATALOG_ALIASES`.  Numeric fields have decimal commas converted to
    dots and coerced to proper numeric types.
    """

    p = Path(path)
    if p.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
        df = pd.read_excel(p, dtype=str)
    else:
        df = pd.read_csv(p, dtype=str)
    df = _rename_with_aliases(df, CATALOG_ALIAS_MAP)
    for col in NUMERIC_COLS & set(df.columns):
        df[col] = df[col].map(_to_number)
    return df


def load_keywords_map(path: str | Path) -> Dict[str, str]:
    """Return ``{keyword: wsm_sifra}`` mapping from ``path``.

    Headers are normalized according to :data:`KEYWORD_ALIASES`.  The returned
    dictionary uses lowercase keywords as keys.
    """

    p = Path(path)
    if p.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
        df = pd.read_excel(p, dtype=str)
    else:
        df = pd.read_csv(p, dtype=str)
    df = _rename_with_aliases(df, KEYWORD_ALIAS_MAP)
    if not {"wsm_sifra", "keyword"} <= set(df.columns):
        return {}
    result: Dict[str, str] = {}
    for _, row in df.dropna(subset=["wsm_sifra", "keyword"]).iterrows():
        key = str(row["keyword"]).strip().lower()
        result[key] = str(row["wsm_sifra"]).strip()
    return result
