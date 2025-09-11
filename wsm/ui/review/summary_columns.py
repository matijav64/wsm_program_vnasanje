"""Shared column definitions for review summary tables."""

from __future__ import annotations

# Mapping of internal DataFrame keys to human-readable column headers.
# Both :mod:`summary_utils` and :mod:`gui` import these definitions so that
# changes in one place are reflected everywhere.
SUMMARY_COLUMN_DEFS = [
    ("wsm_sifra", "WSM šifra"),
    ("wsm_naziv", "WSM Naziv"),
    ("kolicina_norm", "Količina"),
    ("vrnjeno", "Vrnjeno"),
    ("vrednost", "Znesek"),
    ("rabata_pct", "Rabat (%)"),
    ("neto_po_rabatu", "Neto po rabatu"),
]

# Column headers used by :func:`summary_df_from_records` and displayed in the
# GUI.
SUMMARY_COLS = [header for _, header in SUMMARY_COLUMN_DEFS]

# Internal column keys used in the GUI ``Treeview`` widget.
SUMMARY_KEYS = [key for key, _ in SUMMARY_COLUMN_DEFS]

# Alias for readability when used in the GUI.
SUMMARY_HEADS = SUMMARY_COLS

__all__ = [
    "SUMMARY_COLS",
    "SUMMARY_KEYS",
    "SUMMARY_HEADS",
    "SUMMARY_COLUMN_DEFS",
]
