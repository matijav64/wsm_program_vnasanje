# File: wsm/parsing/eslog.py
# -*- coding: utf-8 -*-
"""
ESLOG 2.0 (INVOIC) parser
=========================
• get_supplier_info()      → (sifra, ime) dobavitelja
• parse_eslog_invoice()    → DataFrame vseh postavk (vključno z _DOC_ vrstico)
• parse_invoice()          → (DataFrame vrstic, header_total) za CLI
• validate_invoice()       → preveri vsoto vrstic proti header_total
"""

from __future__ import annotations
import decimal
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple

import pandas as pd
from .utils import _normalize_date

# Uvoz pomožnih funkcij iz money.py:
from wsm.parsing.money import extract_total_amount, validate_invoice as validate_line_values

# Use higher precision to avoid premature rounding when summing values.
decimal.getcontext().prec = 28  # Python's default precision

log = logging.getLogger(__name__)

# ────────────────────────── pomožne funkcije ──────────────────────────
def _text(el: ET.Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""

def _decimal(el: ET.Element | None) -> Decimal:
    try:
        txt = _text(el).replace(",", ".")
        return Decimal(txt) if txt else Decimal("0")
    except Exception:
        return Decimal("0")


# Namespace za ESLOG (če je prisoten)
NS = {"e": "urn:eslog:2.00"}

# Common document discount codes.  Extend this list or pass a custom
# sequence to ``parse_eslog_invoice`` if your suppliers use different
# identifiers.
DEFAULT_DOC_DISCOUNT_CODES = ["204", "260", "131", "128"]

# ────────────────────────── line helpers ──────────────────────────
def _apply_discount(gross: Decimal, disc: Decimal) -> Decimal:
    """Return ``gross - disc`` rounded to two decimals."""
    return (gross - disc).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _discount_pct(gross: Decimal, pct: Decimal) -> Decimal:
    """Return discount amount from ``pct`` of ``gross`` rounded to two decimals."""
    return (gross * pct / Decimal("100")).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _line_gross(sg26: ET.Element) -> Decimal:
    """Return line gross amount before discounts."""
    for moa in sg26.findall('.//e:S_MOA', NS):
        if _text(moa.find('./e:C_C516/e:D_5025', NS)) == '38':
            return _decimal(moa.find('./e:C_C516/e:D_5004', NS)).quantize(
                Decimal('0.01'), ROUND_HALF_UP
            )

    qty = _decimal(sg26.find('.//e:S_QTY/e:C_C186/e:D_6060', NS))
    price = Decimal('0')
    for pri in sg26.findall('.//e:S_PRI', NS):
        qual = _text(pri.find('./e:C_C509/e:D_5125', NS))
        amt = _decimal(pri.find('./e:C_C509/e:D_5118', NS))
        if qual == 'AAA' and amt != 0 and price == 0:
            price = amt
        if qual == 'AAB' and amt != 0:
            price = amt
            break
    return (price * qty).quantize(Decimal('0.01'), ROUND_HALF_UP)


def _line_discount(sg26: ET.Element) -> Decimal:
    """Return discount amount for the line."""
    for sg39 in sg26.findall('.//e:G_SG39', NS):
        if _text(sg39.find('./e:S_ALC/e:D_5463', NS)) != 'A':
            continue
        # fixed discount amount
        for moa in sg39.findall('.//e:G_SG42/e:S_MOA', NS):
            if _text(moa.find('./e:C_C516/e:D_5025', NS)) == '204':
                amt = _decimal(moa.find('./e:C_C516/e:D_5004', NS))
                return amt.quantize(Decimal('0.01'), ROUND_HALF_UP)
        # percentage discount
        pcd = sg39.find('.//e:S_PCD', NS)
        if pcd is not None and _text(pcd.find('./e:C_C501/e:D_5245', NS)) == '1':
            pct = _decimal(pcd.find('./e:C_C501/e:D_5482', NS))
            if pct != 0:
                gross = _line_gross(sg26)
                return _discount_pct(gross, pct)
    return Decimal('0')


def _line_net(sg26: ET.Element) -> Decimal:
    """Return net line amount (gross minus discount)."""
    gross = _line_gross(sg26)
    disc = _line_discount(sg26)
    return _apply_discount(gross, disc)


def _line_tax(sg26: ET.Element) -> Decimal:
    """Return VAT amount for the line.

    If MOA 124 segments are missing, the amount is calculated from the line
    net value and VAT rate.
    """
    total = Decimal("0")
    for sg34 in sg26.findall('.//e:G_SG34', NS):
        for moa in sg34.findall('./e:S_MOA', NS):
            if _text(moa.find('./e:C_C516/e:D_5025', NS)) == '124':
                total += _decimal(moa.find('./e:C_C516/e:D_5004', NS))

    if total == 0:
        rate = Decimal("0")
        for tax in sg26.findall('.//e:G_SG34/e:S_TAX', NS):
            r = _decimal(tax.find('./e:C_C243/e:D_5278', NS))
            if r != 0:
                rate = r
                break
        if rate != 0:
            total = _line_net(sg26) * rate / Decimal("100")

    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)

# ────────────────────── dobavitelj: koda + ime ──────────────────────
def get_supplier_info(xml_path: str | Path) -> Tuple[str, str]:
    """
    Vrne (sifra, ime) dobavitelja:
    • najprej <S_NAD> z D_3035 = "SU"
    • če ni "SU", išče "SE"
    Če ni najdeno, vrne ("", "").
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        seller_code = seller_name = ""
        nodes = root.findall(".//e:S_NAD", NS)
        if not nodes:
            # fallback: poiščemo vse elemente <S_NAD> po local-name
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

# ────────────────────── dobavitelj: koda + ime + davčna ──────────────────────
def get_supplier_info_vat(xml_path: str | Path) -> Tuple[str, str, str | None]:
    """Return supplier code, name and VAT number if available."""
    code, name = get_supplier_info(xml_path)
    vat: str | None = None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Locate the seller party (SU or SE) and search only within that group
        seller_group = None
        for sg2 in root.findall(".//e:G_SG2", NS):
            nad = sg2.find("./e:S_NAD", NS)
            if nad is not None:
                typ_el = nad.find("./e:D_3035", NS) or next(
                    (el for el in nad.iter() if el.tag.split("}")[-1] == "D_3035"),
                    None,
                )
                if _text(typ_el) in {"SU", "SE"}:
                    seller_group = sg2
                    break

        search_root = seller_group if seller_group is not None else root

        fallback_vat = None
        for sg3 in search_root.findall("./e:G_SG3", NS):
            rff = sg3.find("./e:S_RFF", NS)
            if rff is None:
                continue
            code_el = rff.find("./e:C_C506/e:D_1153", NS) or next(
                (el for el in rff.iter() if el.tag.split("}")[-1] == "D_1153"),
                None,
            )
            val_el = rff.find("./e:C_C506/e:D_1154", NS) or next(
                (el for el in rff.iter() if el.tag.split("}")[-1] == "D_1154"),
                None,
            )
            rff_code = _text(code_el)
            vat_val = _text(val_el)
            if rff_code == "VA" and vat_val:
                vat = vat_val
                break
            if fallback_vat is None and (
                rff_code in {"AHP", "0199"} or vat_val.startswith("SI")
            ):
                fallback_vat = vat_val

        if vat is None:
            vat = fallback_vat

        if vat is None:
            for com in search_root.findall(".//e:S_COM", NS):
                com_code = _text(com.find("./e:C_C076/e:D_3155", NS))
                if com_code == "9949":
                    vat_val = _text(com.find("./e:C_C076/e:D_3148", NS))
                    if vat_val:
                        vat = vat_val
                        break

        if vat:
            vat = vat.replace(" ", "").upper()
            if not re.match(r"^SI\d{8}$", vat):
                vat = None
    except Exception:
        vat = None
    return code, name, vat

# ─────────────────────── vsota iz glave ───────────────────────
def extract_header_net(xml_path: Path | str) -> Decimal:
    """Vrne znesek iz MOA 389 (neto brez DDV)."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for moa in root.findall('.//e:G_SG50/e:S_MOA', NS):
            if _text(moa.find('./e:C_C516/e:D_5025', NS)) == '389':
                return _decimal(moa.find('./e:C_C516/e:D_5004', NS))
    except Exception:
        pass
    return Decimal('0')


def extract_grand_total(xml_path: Path | str) -> Decimal:
    """Return invoice grand total from MOA 9."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for moa in root.findall('.//e:G_SG50/e:S_MOA', NS):
            if _text(moa.find('./e:C_C516/e:D_5025', NS)) == '9':
                return _decimal(moa.find('./e:C_C516/e:D_5004', NS))
    except Exception:
        pass
    return Decimal('0')

# ───────────────────── datum opravljene storitve ─────────────────────
def extract_service_date(xml_path: Path | str) -> str | None:
    """Vrne datum opravljene storitve (DTM 35) ali datum računa (DTM 137)."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for dtm in root.findall('.//e:S_DTM', NS):
            if _text(dtm.find('./e:C_C507/e:D_2005', NS)) == '35':
                date = _text(dtm.find('./e:C_C507/e:D_2380', NS))
                if date:
                    return _normalize_date(date)
        for dtm in root.findall('.//e:S_DTM', NS):
            if _text(dtm.find('./e:C_C507/e:D_2005', NS)) == '137':
                date = _text(dtm.find('./e:C_C507/e:D_2380', NS))
                if date:
                    return _normalize_date(date)
    except Exception:
        pass
    return None

# ───────────────────── številka računa ─────────────────────
def extract_invoice_number(xml_path: Path | str) -> str | None:
    """Vrne številko računa iz segmenta BGM (D_1004)."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        bgm = root.find('.//e:S_BGM', NS)
        if bgm is None:
            for node in root.iter():
                if node.tag.split('}')[-1] == 'S_BGM':
                    bgm = node
                    break
        if bgm is not None:
            num_el = bgm.find('.//e:C_C106/e:D_1004', NS)
            if num_el is None:
                num_el = next((el for el in bgm.iter() if el.tag.split('}')[-1] == 'D_1004'), None)
            if num_el is not None:
                num = _text(num_el)
                if num:
                    return num
    except Exception:
        pass
    return None

# ──────────────────── glavni parser za ESLOG INVOIC ────────────────────
def parse_eslog_invoice(
    xml_path: str | Path,
    discount_codes: List[str] | None = None,
) -> tuple[pd.DataFrame, bool]:
    """
    Parsira ESLOG INVOIC XML in vrne DataFrame vseh postavk:
      • glavne postavke <G_SG26>
      • morebiten dokumentarni popust (_DOC_ vrstico)
    Stolpci v DataFrame:
      - sifra_dobavitelja (string)
      - naziv            (string)
      - kolicina         (Decimal)
      - enota            (string)
      - cena_bruto       (Decimal)
      - cena_netto       (Decimal)
      - rabata           (Decimal)
      - rabata_pct       (Decimal)
      - vrednost         (Decimal)
      - ddv_stopnja      (Decimal)
      - sifra_artikla    (string)

    Parameters
    ----------
    xml_path : str | Path
        Pot do eSLOG XML datoteke.
    discount_codes : list[str] | None, optional
        Seznam kod za dokumentarni popust.  Privzeto je
        ``DEFAULT_DOC_DISCOUNT_CODES``.
    Vrne tudi ``bool`` flag, ki označuje ali vsota izračunanih
    vrednosti (neto + DDV) ustreza znesku iz segmenta ``MOA 9``.
    """
    supplier_code, _ = get_supplier_info(xml_path)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    items: List[Dict] = []
    net_total = Decimal("0")
    tax_total = Decimal("0")

    # ───────────── LINE ITEMS ─────────────
    for sg26 in root.findall(".//e:G_SG26", NS):
        qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
        if qty == 0:
            continue
        unit = _text(sg26.find(".//e:S_QTY/e:C_C186/e:D_6411", NS))

        # poiščemo šifro artikla
        art_code = ""
        lin_code = _text(sg26.find(".//e:S_LIN/e:C_C212/e:D_7140", NS))
        art_code = re.sub(r"\D+", "", lin_code)
        if not art_code:
            pia_first = sg26.find(".//e:S_PIA/e:C_C212/e:D_7140", NS)
            if pia_first is not None:
                art_code = re.sub(r"\D+", "", pia_first.text or "")

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
        net_amount_moa: Decimal | None = None
        for moa in sg26.findall(".//e:S_MOA", NS):
            if _text(moa.find("./e:C_C516/e:D_5025", NS)) == "203":
                net_amount_moa = (
                    _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                    .quantize(Decimal("0.01"), ROUND_HALF_UP)
                )
                break

        calc_net = _line_net(sg26)
        if net_amount_moa is None:
            net_amount = calc_net
        else:
            net_amount = net_amount_moa
            if net_amount != calc_net:
                log.warning(
                    "Line net mismatch: MOA 203 %s vs calculated %s",
                    net_amount,
                    calc_net,
                )

        # stopnja DDV (npr. 9.5 ali 22)
        vat_rate = Decimal("0")
        for tax in sg26.findall(".//e:G_SG34/e:S_TAX", NS):
            rate = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
            if rate != 0:
                vat_rate = rate
                break

        # rabat na ravni vrstice
        rebate = _line_discount(sg26)
        explicit_pct: Decimal | None = None
        for sg39 in sg26.findall(".//e:G_SG39", NS):
            if _text(sg39.find("./e:S_ALC/e:D_5463", NS)) != "A":
                continue
            pcd = sg39.find("./e:S_PCD", NS)
            if pcd is not None and _text(pcd.find("./e:C_C501/e:D_5245", NS)) == "1":
                pct = _decimal(pcd.find("./e:C_C501/e:D_5482", NS))
                if pct != 0:
                    explicit_pct = pct.quantize(Decimal("0.01"), ROUND_HALF_UP)
                    break


        # izračun cen pred in po rabatu
        if qty:
            cena_pred = ((net_amount + rebate) / qty).quantize(Decimal("0.0001"))
            cena_post = (net_amount / qty).quantize(Decimal("0.0001"))
        else:
            cena_pred = cena_post = Decimal("0")

        if explicit_pct is not None:
            rabata_pct = explicit_pct
        else:
            if rebate > 0 and qty and cena_pred > 0:

                rabata_pct = (
                    (rebate / qty) / cena_pred * Decimal("100")
                ).quantize(Decimal("0.01"), ROUND_HALF_UP)

            else:
                rabata_pct = Decimal("0.00")

        is_gratis = rabata_pct >= Decimal("99.9")

        line_tax = _line_tax(sg26)
        net_total += net_amount
        tax_total += line_tax

        items.append({
            "sifra_dobavitelja": supplier_code,
            "naziv":            desc,
            "kolicina":         qty,
            "enota":            unit,
            "cena_bruto":       cena_pred,
            "cena_netto":       cena_post,
            "rabata":           rebate,
            "rabata_pct":       rabata_pct,
            "is_gratis":        is_gratis,
            "vrednost":         net_amount,
            "ddv_stopnja":      vat_rate,
            "sifra_artikla":    art_code,
        })

    # ───────── DOCUMENT DISCOUNT (če obstaja) ─────────
    discount_codes = list(discount_codes or DEFAULT_DOC_DISCOUNT_CODES)
    discounts = {code: Decimal("0") for code in discount_codes}
    seen_values: set[Decimal] = set()
    for seg in root.findall(".//e:G_SG50", NS) + root.findall(".//e:G_SG20", NS):
        for moa in seg.findall(".//e:S_MOA", NS):
            code = _text(moa.find("./e:C_C516/e:D_5025", NS))
            if code in discounts:
                amt = (
                    _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                    .quantize(Decimal("0.01"), ROUND_HALF_UP)
                )
                if amt in seen_values:
                    continue
                seen_values.add(amt)
                discounts[code] += amt

    # Sum all discount code amounts instead of only the first
    doc_discount = sum(
        (discounts.get(code) or Decimal("0")) for code in discount_codes
    ).quantize(Decimal("0.01"), ROUND_HALF_UP)


    if doc_discount != 0:
        net_total -= doc_discount
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
            "is_gratis":        False,
        })

    df = pd.DataFrame(items)
    if not df.empty:
        df.sort_values(["sifra_dobavitelja", "naziv"], inplace=True, ignore_index=True)

    calculated_total = (net_total + tax_total).quantize(Decimal("0.01"), ROUND_HALF_UP)
    grand_total = extract_grand_total(xml_path)
    ok = True
    if grand_total != 0 and abs(calculated_total - grand_total) > Decimal("0.01"):
        log.warning(
            "Invoice total mismatch: MOA 9 %s vs calculated %s",
            grand_total,
            calculated_total,
        )
        ok = False

    return df, ok

# ───────────────────── PRILAGOJENA funkcija za CLI ─────────────────────
def parse_invoice(source: str | Path):
    """
    Parsira e-račun (ESLOG INVOIC) iz XML ali PDF (če je implementirano).
    Vrne:
      • df: DataFrame s stolpci ['cena_netto','kolicina','rabata_pct','izracunana_vrednost']
        (vrednosti so Decimal v object stolpcih)
      • header_total: Decimal (InvoiceTotal – DocumentDiscount)
    Uporablja se v CLI (wsm/cli.py).
    """
    # naložimo XML
    if isinstance(source, (str, Path)) and Path(source).exists():
        tree = ET.parse(source)
        root = tree.getroot()
    else:
        root = ET.fromstring(source)

    # Ali je pravi eSLOG (urn:eslog:2.00)?
    if root.tag.endswith('Invoice') and root.find('.//e:M_INVOIC', NS) is not None:
        df_items, totals_ok = parse_eslog_invoice(source)
        header_total = extract_header_net(Path(source) if isinstance(source, (str, Path)) else source)
        df = pd.DataFrame({
            'cena_netto': df_items['cena_netto'],
            'kolicina': df_items['kolicina'],
            'rabata_pct': df_items['rabata_pct'],
            'izracunana_vrednost': df_items['vrednost'],
        }, dtype=object)
        return df, header_total, totals_ok

    # Preprost <Racun> format z elementi <Postavka>
    if root.tag == 'Racun' or root.find('Postavka') is not None:
        header_total = extract_total_amount(root)
        rows = []
        for line in root.findall('Postavka'):
            name = line.findtext('Naziv') or ''
            qty_str = line.findtext('Kolicina') or '0'
            price_str = line.findtext('Cena') or '0'
            unit = line.attrib.get('enota', '').strip().lower()
            if not unit:
                name_l = name.lower()
                if re.search(r"\bkos\b", name_l):
                    unit = 'kos'
                elif re.search(r"\bkg\b", name_l):
                    unit = 'kg'
            price = Decimal(price_str.replace(',', '.'))
            qty = Decimal(qty_str.replace(',', '.'))
            izracun_val = (price * qty).quantize(Decimal('0.01'), ROUND_HALF_UP)
            rows.append({
                'cena_netto': price,
                'kolicina': qty,
                'rabata_pct': Decimal('0'),
                'izracunana_vrednost': izracun_val,
                'enota': unit,
                'naziv': name,
            })
        df = pd.DataFrame(rows, dtype=object)
        return df, header_total, True

    # izvzamemo glavo (InvoiceTotal – DocumentDiscount)
    header_total = extract_total_amount(root)

    # preberemo vse <LineItems/LineItem>
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
        ).quantize(Decimal("0.01"), ROUND_HALF_UP)

        rows.append({
            "cena_netto":           cena,
            "kolicina":             kolic,
            "rabata_pct":           rabata_pct,
            "izracunana_vrednost":  izracun_val,
        })

    # Če ni nobenih vrstic, naredimo prazen DataFrame z ustreznimi stolpci
    if not rows:
        df = pd.DataFrame(columns=[
            "cena_netto", "kolicina", "rabata_pct", "izracunana_vrednost"
        ])
    else:
        df = pd.DataFrame(rows, dtype=object)

    return df, header_total, True

def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri vsoto vseh izracunana_vrednost (Decimal) proti header_total.
    Če stolpec ne obstaja (težak primer z nenavadno strukturo XML), vrne False.
    """
    # Preverimo, ali je stolpec sploh prisoten
    if "izracunana_vrednost" not in df.columns:
        return False

    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    return validate_line_values(df, header_total)
