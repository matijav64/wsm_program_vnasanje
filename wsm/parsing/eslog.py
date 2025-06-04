# File: wsm/parsing/eslog.py
# -*- coding: utf-8 -*-
"""
ESLOG 2.0 (INVOIC) parser
=========================
• get_supplier_info()  → (šifra, ime) dobavitelja  
  – najprej NAD+SU (Supplier)
  – če SU ni, uporabimo NAD+SE (Seller)
• parse_eslog_invoice() → DataFrame postavk + popusti
• parse_invoice()        → DataFrame postavk + header_total (za CLI)
• validate_invoice()     → preveri vsoto vrstičnih vrednosti proti header_total
"""

from __future__ import annotations
import decimal
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple

import pandas as pd

# Funkcija iz money.py, ki vrne glavo (InvoiceTotal – DocumentDiscount)
from wsm.parsing.money import extract_total_amount, validate_invoice as validate_line_values

decimal.getcontext().prec = 12  # cent-natančno računanje

# ───────────────────────── help funkcije ─────────────────────────
def _text(el: ET.Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""

def _decimal(el: ET.Element | None) -> Decimal:
    try:
        txt = _text(el).replace(",", ".")
        return Decimal(txt) if txt else Decimal("0")
    except Exception:
        return Decimal("0")

NS = {"e": "urn:eslog:2.00"}

# ───────────────────── dobavitelj: koda + ime ───────────────────────
def get_supplier_info(xml_path: str | Path) -> Tuple[str, str]:
    """
    Vrne (šifra, ime) dobavitelja:
    • najprej NAD+SU (Supplier)
    • če SU ni, uporabi NAD+SE (Seller)
    Iskanje po local-name, če namespace ni točen.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        seller_code = seller_name = ""

        nodes = root.findall(".//e:S_NAD", NS)
        if not nodes:
            nodes = [el for el in root.iter() if el.tag.split("}")[-1] == "S_NAD"]

        for nad in nodes:
            typ_el = nad.find("./e:D_3035", NS) or next((el for el in nad if el.tag.split("}")[-1] == "D_3035"), None)
            typ = _text(typ_el)
            if typ == "SU":
                code_el = nad.find(".//e:C_C082/e:D_3039", NS) or next((el for el in nad.iter() if el.tag.split("}")[-1] == "D_3039"), None)
                name_els = nad.findall(".//e:C_C080/e:D_3036", NS)
                name = " ".join(_text(el) for el in name_els if _text(el))
                return _text(code_el), name

            if typ == "SE" and not seller_name:
                code_el = nad.find(".//e:C_C082/e:D_3039", NS) or next((el for el in nad.iter() if el.tag.split("}")[-1] == "D_3039"), None)
                name_els = nad.findall(".//e:C_C080/e:D_3036", NS)
                name = " ".join(_text(el) for el in name_els if _text(el))
                seller_code = _text(code_el)
                seller_name = name

        return seller_code, seller_name
    except Exception:
        return "", ""

def get_supplier_name(xml_path: str | Path) -> Optional[str]:
    _, name = get_supplier_info(xml_path)
    return name or None

# ───────────────────────── glavni parser za ESLOG (INVOIC) ────────────────────────────
def parse_eslog_invoice(xml_path: str | Path, sup_map: dict) -> pd.DataFrame:
    """
    Parsira ESLOG INVOIC XML in vrne DataFrame z vsemi postavkami, vključno
    z morebitnim dokumentarnim popustom (_DOC_ vrstica).
    """
    supplier_code, _ = get_supplier_info(xml_path)
    override_H87 = sup_map.get(supplier_code, {}).get("override_H87_to_kg", False)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    items: List[Dict] = []

    # ---------------------- LINE ITEMS ----------------------
    for sg26 in root.findall(".//e:G_SG26", NS):
        qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
        if qty == 0:
            continue
        unit = _text(sg26.find(".//e:S_QTY/e:C_C186/e:D_6411", NS))
        if override_H87 and unit == "H87":
            unit = "kg"

        # opcijska šifra artikla
        art_code = ""
        for pia in sg26.findall(".//e:S_PIA", NS):
            if _text(pia.find("./e:C_C212/e:D_7143", NS)) == "SA":
                art_code = _text(pia.find("./e:C_C212/e:D_7140", NS))
                break
        if not art_code:
            fb = _text(sg26.find(".//e:S_LIN/e:C_C212/e:D_7140", NS))
            art_code = fb if fb.isdigit() else ""

        desc = _text(sg26.find(".//e:S_IMD/e:C_C273/e:D_7008", NS))

        # cene AAA/AAB
        price_net = price_gross = Decimal("0")
        for pri in sg26.findall(".//e:S_PRI", NS):
            qual = _text(pri.find("./e:C_C509/e:D_5125", NS))
            amt = _decimal(pri.find("./e:C_C509/e:D_5118", NS))
            if qual == "AAA":
                price_net = amt
            elif qual == "AAB":
                price_gross = amt
        if price_gross == 0:
            price_gross = price_net

        # neto znesek vrstice (MOA 203)
        net_amount = Decimal("0")
        for moa in sg26.findall(".//e:S_MOA", NS):
            if _text(moa.find("./e:C_C516/e:D_5025", NS)) == "203":
                net_amount = _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                break

        # rabat na ravni vrstice
        rebate = Decimal("0")
        explicit_pct: Decimal | None = None
        for sg39 in sg26.findall(".//e:G_SG39", NS):
            if _text(sg39.find("./e:S_ALC/e:D_5463", NS)) != "A":
                continue
            pct = _decimal(sg39.find("./e:S_PCD/e:C_C501/e:D_5482", NS))
            if pct != 0:
                explicit_pct = pct.quantize(Decimal("0.01"))
            for moa in sg39.findall(".//e:G_SG42/e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == "204":
                    rebate += _decimal(moa.find("./e:C_C516/e:D_5004", NS))

        # izračun cen pred in po rabatu, odstotek rabata
        if qty:
            cena_pred = ((net_amount + rebate) / qty).quantize(Decimal("0.0001"))
            cena_post = (net_amount / qty).quantize(Decimal("0.0001"))
        else:
            cena_pred = cena_post = Decimal("0")

        if explicit_pct is not None:
            rabata_pct = explicit_pct
        else:
            if rebate > 0 and qty and cena_pred > 0:
                rabata_pct = ((rebate / qty) / cena_pred * Decimal("100")).quantize(Decimal("0.01"))
            else:
                rabata_pct = Decimal("0.00")

        items.append({
            "sifra_dobavitelja": supplier_code,
            "naziv":            desc,
            "kolicina":         qty,
            "enota":            unit,
            "cena_bruto":       cena_pred,
            "cena_netto":       cena_post,
            "rabata":           rebate,
            "rabata_pct":       rabata_pct,
            "vrednost":         net_amount,
            "sifra_artikla":    art_code,
        })

    # ------------------- DOCUMENT REBATE (če obstaja) -------------------
    doc_discount = Decimal("0")
    for seg in root.findall(".//e:G_SG50", NS) + root.findall(".//e:G_SG20", NS):
        for moa in seg.findall(".//e:S_MOA", NS):
            if _text(moa.find("./e:C_C516/e:D_5025", NS)) in {"204", "260"}:
                doc_discount += _decimal(moa.find("./e:C_C516/e:D_5004", NS))

    if doc_discount != 0:
        items.append({
            "sifra_dobavitelja": "_DOC_",
            "naziv":            "Popust na ravni računa",
            "kolicina":         Decimal("1"),
            "enota":            "",
            "cena_bruto":       doc_discount,
            "cena_netto":       Decimal("0"),
            "rabata":           doc_discount,
            "rabata_pct":       Decimal("100.00"),
            "vrednost":         -doc_discount,
        })

    df = pd.DataFrame(items)
    if not df.empty:
        df.sort_values(["sifra_dobavitelja", "naziv"], inplace=True, ignore_index=True)
    return df

# ───────────────────────── PRILAGOJENA funkcija za CLI ────────────────────────────
def parse_invoice(source: str | Path):
    """
    Parsira e-račun (ESLOG INVOIC) iz XML ali PDF (če je implementirano).
    Vrne (DataFrame, header_total):
      • DataFrame: ['cena_netto','kolicina','rabata_pct','izracunana_vrednost']
      • header_total: Decimal(glava minus dokumentarni popust)
    Ta funkcija je poklicana iz CLI (wsm/cli.py).
    """
    if isinstance(source, (str, Path)) and Path(source).exists():
        tree = ET.parse(source)
        root = tree.getroot()
    else:
        root = ET.fromstring(source)

    # header_total z upoštevanim dokumentarnim popustom
    header_total = extract_total_amount(root)

    rows = []
    for li in root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        izracun_val = (
            cena
            * kolic
            * (Decimal("1") - rabata_pct / Decimal("100"))
        ).quantize(Decimal("0.01"))

        rows.append({
            "cena_netto": float(cena),
            "kolicina": float(kolic),
            "rabata_pct": float(rabata_pct),
            "izracunana_vrednost": float(izracun_val),
        })

    df = pd.DataFrame(rows)
    return df, header_total

def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izračunanih vrstičnih vrednosti ujema z header_total
    (upoštevano že obdelano vrednost z extract_total_amount). Toleranca 0.05 €.
    """
    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    return validate_line_values(df, header_total)
