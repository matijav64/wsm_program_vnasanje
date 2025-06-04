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
    """Return the final invoice total after any document discounts."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def find_moas(code: str) -> list[Decimal]:
        values = []
        for moa in root.findall(".//e:S_MOA", _eNS):
            if _text(moa.find("./e:C_C516/e:D_5025", _eNS)) == code:
                values.append(_dec(_text(moa.find("./e:C_C516/e:D_5004", _eNS))))
        return values

    # 1) Prefer MOA 389 if present (amount payable)
    vals_389 = find_moas("389")
    if vals_389:
        return vals_389[0]

    # 2) Determine whether to use MOA 86 (Mercator) or 79
    sup_name = None
    for nad in root.findall(".//e:S_NAD", _eNS):
        if _text(nad.find("./e:D_3035", _eNS)) == "SU":
            c080 = nad.find("./e:C_C080", _eNS)
            if c080 is not None:
                sup_name = _text(c080.find("./e:D_3036", _eNS))
            break

    moa_code = "86" if sup_name and "MERCATOR" in sup_name.upper() else "79"
    totals = find_moas(moa_code)
    if not totals:
        return Decimal("0")
    total = totals[0]

    # 3) Subtract document level discounts (MOA 260) if any
    discount = sum(find_moas("260"))
    return total - discount


def parse_invoice_currency(xml_path: str | Path) -> str:
    """Return the invoice currency (D_6345) if present, otherwise 'EUR'."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        cur = root.find(".//e:D_6345", _eNS)
        code = _text(cur)
        return code if code else "EUR"
    except Exception:
        return "EUR"
