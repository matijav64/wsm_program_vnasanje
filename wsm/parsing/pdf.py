# File: wsm/parsing/pdf.py
# -*- coding: utf-8 -*-
"""
PDF parser + util za ekstrakcijo imena dobavitelja.
"""
from __future__ import annotations
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber
from .utils import _normalize_date


# ───────────────────── ime dobavitelja (za CLI) ──────────────────────
def get_supplier_name_from_pdf(pdf_path: str | Path) -> Optional[str]:
    """
    Poskusi iz prvih 2 strani PDF‑ja izluščiti ime dobavitelja.
    Hevristike:
      • vrstica z 'Dobavitelj:' ali 'Supplier:'
      • prva vrstica, ki vsebuje 'd.o.o.' ali 'd.d.'
    """
    rx_label = re.compile(r"(?:dobavitelj|supplier)\s*:?\s*(.+)", re.I)
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:2]:
            txt = page.extract_text() or ""
            for line in txt.split("\n"):
                m = rx_label.search(line)
                if m:
                    return m.group(1).strip()
                if any(tag in line.lower() for tag in ("d.o.o.", "d.d.")):
                    return line.strip()
    return None


# ───────────────────────── glavni parser ─────────────────────────────
def parse_pdf(pdf_path: str | Path) -> pd.DataFrame:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            df = pd.DataFrame(table[1:], columns=table[0])

            if "Šifra" not in df.columns or "Količina" not in df.columns:
                continue  # preskoči strani brez postavk

            df = df.rename(
                columns={
                    "Naziv": "naziv",
                    "Šifra": "sifra_dobavitelja",
                    "Količina": "kolicina",
                    "ME": "enota",
                    "NETO cena": "neto_cena",
                    "Vred. brez DDV": "vrednost",
                }
            )

            # pretvori v Decimal
            for col in ("kolicina", "neto_cena", "vrednost"):
                df[col] = (
                    df[col]
                    .str.replace(r"\.", "", regex=True)
                    .str.replace(",", ".")
                    .apply(
                        lambda s: Decimal(s).quantize(
                            Decimal("0.01"), ROUND_HALF_UP
                        )
                    )
                )
            df = df[df["sifra_dobavitelja"].notna() & df["naziv"].notna()]
            pages.append(
                df[
                    [
                        "sifra_dobavitelja",
                        "naziv",
                        "kolicina",
                        "enota",
                        "neto_cena",
                        "vrednost",
                    ]
                ]
            )

    if not pages:
        raise ValueError(f"No invoice table found in {pdf_path!r}")
    return pd.concat(pages, ignore_index=True)


# --- Helper functions for service date and invoice number ---
_date_label_rx = re.compile(
    r"(?:Datum\s+storitve|Service\s+date|Datum\s+opravljene\s+storitve)", re.I
)
_date_value_rx = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.?\s*\d{1,2}\.?\s*\d{4})"
)
_invoice_label_rx = re.compile(
    r"(?:\u0160t\.\s*ra\u010duna|Invoice\s*no\.?|Invoice\s*number)", re.I
)
_invoice_value_rx = re.compile(r"([A-Za-z0-9-_/]+)")


def extract_service_date(pdf_path: Path) -> str | None:
    """Extract service date from first PDF pages if possible."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:2]:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                if _date_label_rx.search(line):
                    m = _date_value_rx.search(line)
                    if m:
                        return _normalize_date(m.group(1))
            # look for label followed by next line value
            lines = text.split("\n")
            for i, line in enumerate(lines[:-1]):
                if _date_label_rx.search(line) and _date_value_rx.search(
                    lines[i + 1]
                ):
                    return _normalize_date(
                        _date_value_rx.search(lines[i + 1]).group(1)
                    )
    return None


def extract_invoice_number(pdf_path: Path) -> str | None:
    """Extract invoice number from first PDF pages if possible."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:2]:
            text = page.extract_text() or ""
            lines = text.split("\n")
            for idx, line in enumerate(lines):
                if _invoice_label_rx.search(line):
                    m = _invoice_value_rx.search(
                        line[_invoice_label_rx.search(line).end() :]
                    )
                    if m:
                        return m.group(1).strip()
                    if idx + 1 < len(lines):
                        m = _invoice_value_rx.search(lines[idx + 1])
                        if m:
                            return m.group(1).strip()
    return None
