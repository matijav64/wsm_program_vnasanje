from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, IO
import unicodedata
import re
import logging

import pandas as pd

__all__ = ["load_catalog", "load_keywords_map"]


log = logging.getLogger(__name__)


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
    "cena": {"cena", "price", "neto", "unit price", "zadnja nabavna cena"},
}
CATALOG_ALIAS_MAP = _build_alias_map(CATALOG_ALIASES)

# Aliases for keyword files
KEYWORD_ALIASES = {
    "wsm_sifra": {"wsm sifra", "šifra", "sifra", "code"},
    "keyword": {"keyword", "kljucna beseda", "kljucnabeseda"},
    "sifra_dobavitelja": {
        "dobavitelj",
        "supplier",
        "supplier_code",
        "supplier code",
    },
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


def _read_table(path_or_buf: str | Path | IO[Any]) -> pd.DataFrame:
    """Return DataFrame from ``path_or_buf`` as Excel or CSV.

    ``path_or_buf`` may be a filesystem path or a file-like object.  The
    function tries to read Excel first and falls back to CSV if that fails.
    """

    if hasattr(path_or_buf, "read"):
        try:
            return pd.read_excel(path_or_buf, dtype=str)
        except Exception:  # pragma: no cover - defensive
            path_or_buf.seek(0)
            return pd.read_csv(path_or_buf, dtype=str)
    p = Path(path_or_buf)
    if p.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
        return pd.read_excel(p, dtype=str)
    return pd.read_csv(p, dtype=str)


def load_catalog(path: str | Path | IO[Any]) -> pd.DataFrame:
    """Return normalized catalog data from ``path``.

    ``path`` may be a filesystem path or a file-like object.  Column headers
    are matched case-insensitively and without diacritics using
    :data:`CATALOG_ALIASES`.  Numeric fields have decimal commas converted to
    dots and coerced to proper numeric types.
    """

    df = _read_table(path)
    df = _rename_with_aliases(df, CATALOG_ALIAS_MAP)
    for col in NUMERIC_COLS & set(df.columns):
        df[col] = df[col].map(_to_number)
    return df


def load_keywords_map(
    path: str | Path | IO[Any], supplier_code: str | None = None
) -> Dict[str, str]:
    """Return ``{keyword: wsm_sifra}`` mapping from ``path``.

    ``path`` may be a filesystem path or a file-like object.  Headers are
    normalized according to :data:`KEYWORD_ALIASES`.  If ``supplier_code`` is
    provided and the file contains a ``sifra_dobavitelja`` column, only rows for
    that supplier are used.  The returned dictionary uses lowercase keywords as
    keys.  When the same keyword maps to multiple codes, the first occurrence is
    kept and subsequent conflicting entries are ignored.  A warning is logged
    listing all conflicting codes.
    """

    df = _read_table(path)
    df = _rename_with_aliases(df, KEYWORD_ALIAS_MAP)
    if supplier_code and "sifra_dobavitelja" in df.columns:
        df = df[df["sifra_dobavitelja"].astype(str) == str(supplier_code)]
    if not {"wsm_sifra", "keyword"} <= set(df.columns):
        return {}
    result: Dict[str, str] = {}
    duplicates: Dict[str, set[str]] = {}
    for _, row in df.dropna(subset=["wsm_sifra", "keyword"]).iterrows():
        key = str(row["keyword"]).strip().lower()
        code = str(row["wsm_sifra"]).strip()
        existing = result.get(key)
        if existing is None:
            result[key] = code
        elif existing != code:
            duplicates.setdefault(key, {existing}).add(code)
    for key, codes in duplicates.items():
        log.warning(
            "Duplicate keyword '%s' found for codes: %s",
            key,
            ", ".join(sorted(codes)),
        )
    return result
