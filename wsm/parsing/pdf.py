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

            df = df.rename(columns={
                "Naziv":          "naziv",
                "Šifra":          "sifra_dobavitelja",
                "Količina":       "kolicina",
                "ME":             "enota",
                "NETO cena":      "neto_cena",
                "Vred. brez DDV": "vrednost",
            })

            # pretvori v Decimal
            for col in ("kolicina", "neto_cena", "vrednost"):
                df[col] = (df[col].str.replace(r"\.", "", regex=True)
                                   .str.replace(",", ".")
                                   .apply(lambda s: Decimal(s)
                                                   .quantize(Decimal("0.01"), ROUND_HALF_UP)))
            df = df[df["sifra_dobavitelja"].notna() & df["naziv"].notna()]
            pages.append(df[[
                "sifra_dobavitelja", "naziv", "kolicina",
                "enota", "neto_cena", "vrednost"
            ]])

    if not pages:
        raise ValueError(f"No invoice table found in {pdf_path!r}")
    return pd.concat(pages, ignore_index=True)
