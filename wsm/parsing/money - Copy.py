# wsm/parsing/money.py
# -*- coding: utf-8 -*-
"""
money.py – utilities for extracting invoice totals
"""
from pathlib import Path
from decimal import Decimal
import xml.etree.ElementTree as ET

_eNS = {"e": "urn:eslog:2.00"}

def _text(el):
    return el.text.strip() if el is not None and el.text else ""

def _dec(val: str) -> Decimal:
    try:
        return Decimal(val.replace(",", "."))
    except:
        return Decimal("0")

def parse_invoice_total(xml_path: str | Path) -> Decimal:
    """
    Extract the invoice net total (MOA 79) from anywhere in the document.
    If missing, returns 0.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    for moa in root.findall(".//e:S_MOA", _eNS):
        if _text(moa.find("./e:C_C516/e:D_5025", _eNS)) == "79":
            return _dec(_text(moa.find("./e:C_C516/e:D_5004", _eNS)))

    return Decimal("0")
