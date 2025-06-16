# File: wsm/parsing/utils.py
"""Utility helpers for parsers."""
from __future__ import annotations
import re

def _normalize_date(date_str: str) -> str:
    """Convert ``DD.MM.YYYY`` or ``YYYYMMDD`` and similar into ``YYYY-MM-DD``."""
    s = date_str.replace(" ", "").replace("\xa0", "")
    m = re.match(r"(\d{4})(\d{2})(\d{2})$", s)
    if m:
        y, mth, d = m.groups()
        return f"{y}-{mth}-{d}"
    m = re.match(r"(\d{1,2})\.?\s*(\d{1,2})\.?\s*(\d{4})$", s)
    if m:
        d, mth, y = m.groups()
        return f"{y}-{int(mth):02d}-{int(d):02d}"
    return s
