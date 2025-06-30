"""Backward compatibility module for review utilities."""
from __future__ import annotations

from .review.helpers import _fmt, _norm_unit, PRICE_DIFF_THRESHOLD
from .review.gui import review_links, _apply_price_warning
from .review.io import _save_and_close, _load_supplier_map, _write_supplier_map

__all__ = [
    "_fmt",
    "_norm_unit",
    "PRICE_DIFF_THRESHOLD",
    "review_links",
    "_apply_price_warning",
    "_save_and_close",
    "_load_supplier_map",
    "_write_supplier_map",
]
