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
    Extract the invoice total based on supplier:
    - MOA 86 for MERCATOR
    - MOA 79 for other suppliers
    If missing, returns 0.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Find supplier name
    sup_name = None
    for nad in root.findall(".//e:S_NAD", _eNS):
        if _text(nad.find("./e:D_3035", _eNS)) == "SU":
            c080 = nad.find("./e:C_C080", _eNS)
            if c080 is not None:
                sup_name = _text(c080.find("./e:D_3036", _eNS))
            break

    # Decide MOA code
    if sup_name and "MERCATOR" in sup_name.upper():
        moa_code = "86"
    else:
        moa_code = "79"

    # Find MOA with that code
    for moa in root.findall(".//e:S_MOA", _eNS):
        if _text(moa.find("./e:C_C516/e:D_5025", _eNS)) == moa_code:
            return _dec(_text(moa.find("./e:C_C516/e:D_5004", _eNS)))

    return Decimal("0")