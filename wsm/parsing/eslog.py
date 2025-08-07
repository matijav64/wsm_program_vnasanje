# File: wsm/parsing/eslog.py
# -*- coding: utf-8 -*-
"""
ESLOG 2.0 (INVOIC) parser
=========================
• get_supplier_info()      → koda dobavitelja
• parse_eslog_invoice()    → DataFrame vseh postavk (vključno z _DOC_ vrstico)
• parse_invoice()          → (DataFrame vrstic, header_total,
                              discount_total) za CLI
• validate_invoice()       → preveri vsoto vrstic proti header_total
"""

from __future__ import annotations

import decimal
import io
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from defusedxml.common import EntitiesForbidden
from lxml import etree as LET

from .codes import Moa
from .utils import _normalize_date
from wsm.parsing.money import (
    extract_total_amount,
    validate_invoice as validate_line_values,
    calculate_vat,
)

XML_PARSER = LET.XMLParser(resolve_entities=False)

# Use higher precision to avoid premature rounding when summing values.
decimal.getcontext().prec = 28  # Python's default precision
DEC2 = Decimal("0.01")

# module logger
log = logging.getLogger(__name__)


# ────────────────────────── pomožne funkcije ──────────────────────────
def _text(el: LET._Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""


def _decimal(el: LET._Element | None) -> Decimal:
    try:
        txt = _text(el)
        if not txt:
            return Decimal("0")

        txt = txt.replace("\xa0", "").replace(" ", "")
        if "," in txt:
            txt = txt.replace(".", "").replace(",", ".")

        return Decimal(txt)
    except Exception:
        return Decimal("0")


# Namespace za ESLOG (če je prisoten)
NS = {"e": "urn:eslog:2.00"}

# Namespaces for UBL documents
UBL_NS = {
    "cac": (
        "urn:oasis:names:specification:ubl:"
        "schema:xsd:CommonAggregateComponents-2"
    ),
    "cbc": (
        "urn:oasis:names:specification:ubl:"
        "schema:xsd:CommonBasicComponents-2"
    ),
}

# Common document discount codes.  Extend this list or pass a custom
# sequence to ``parse_eslog_invoice`` if your suppliers use different
# identifiers.
DEFAULT_DOC_DISCOUNT_CODES = ["204", "260", "131", "128", "176", "500", "25"]

# Common document charge codes.  These represent additional amounts
# that increase the invoice total.
DEFAULT_DOC_CHARGE_CODES = ["504"]

# Qualifiers used for seller VAT identification in ``S_RFF`` segments.
VAT_QUALIFIERS = {"VA", "0199", "AHP"}


# helper functions -----------------------------------------------------
def _find_gln(nad: LET._Element) -> str:
    """Return GLN from NAD segment if qualifier 0088 is present."""
    for c082 in nad.findall(".//e:C_C082", NS):
        if _text(c082.find("./e:D_1131", NS)) == "0088":
            val = _text(c082.find("./e:D_3039", NS))
            if val:
                return val
    for c082 in nad.findall(".//C_C082"):
        q = c082.find("./D_1131")
        if q is not None and (q.text or "").strip() == "0088":
            code_el = c082.find("./D_3039")
            if code_el is not None and code_el.text:
                return code_el.text.strip()

    gln_el = nad.find(".//e:S_GLN/e:D_7402", NS)
    if gln_el is not None:
        val = _text(gln_el)
        if val:
            return val
    gln_el = nad.find(".//S_GLN/D_7402")
    if gln_el is not None and gln_el.text:
        val = gln_el.text.strip()
        if val:
            return val

    return ""


def _find_any_code(nad: LET._Element) -> str:
    """Return first ``D_3039`` value from NAD segment."""
    code_el = nad.find(".//e:C_C082/e:D_3039", NS)
    if code_el is None:
        code_el = nad.find(".//C_C082/D_3039")
    if code_el is None:
        for el in nad.iter():
            if el.tag.split("}")[-1] == "D_3039":
                code_el = el
                break
    return _text(code_el)


def _find_rff(root: LET._Element, qualifier: str) -> str:
    """Return ``D_1154`` value for the given RFF qualifier."""

    path_ns = f'.//e:S_RFF/e:C_C506[e:D_1153="{qualifier}"]/e:D_1154'
    path_no = f'.//S_RFF/C_C506[D_1153="{qualifier}"]/D_1154'

    el = root.find(path_ns, NS) or root.find(path_no)
    return _text(el)


def _find_vat(grp: LET._Element) -> str:
    """Return VAT number from provided element.

    The helper prefers VAT identifiers declared in UBL structures before
    examining ESLOG specific segments.  It searches ``cac:PartyTaxScheme``
    and ``cac:PartyIdentification`` elements for ``cbc:CompanyID``/``cbc:ID``
    values with a ``schemeID`` of ``VAT`` or ``VA`` and also accepts those
    without a ``schemeID`` attribute.  If nothing is found the function falls
    back to ``S_RFF`` segments with qualifiers ``VA``, ``0199`` or ``AHP``.
    When multiple VAT values are present the first non-empty one is returned
    with ``AHP`` acting as a secondary fallback.
    """

    # --- UBL PartyTaxScheme / PartyIdentification ---
    ubl_paths = [
        ".//cac:PartyTaxScheme/cbc:CompanyID[@schemeID='VAT']",
        ".//cac:PartyTaxScheme/cbc:CompanyID[@schemeID='VA']",
        ".//cac:PartyIdentification/cbc:ID[@schemeID='VAT']",
        ".//cac:PartyIdentification/cbc:ID[@schemeID='VA']",
        ".//cac:PartyTaxScheme/cbc:CompanyID[not(@schemeID) or @schemeID='']",
        ".//cac:PartyIdentification/cbc:ID[not(@schemeID) or @schemeID='']",
    ]
    for path in ubl_paths:
        try:
            vat_nodes = grp.xpath(path, namespaces=UBL_NS)
        except Exception:
            continue
        if vat_nodes:
            vat = _text(vat_nodes[0])
            if vat:
                log.debug("Found VAT in UBL element %s: %s", path, vat)
                return vat

    # --- Custom <VA> element without schemeID ---
    for vat in [
        v.strip()
        for v in grp.xpath(".//*[local-name()='VA']/text()")
        if v.strip()
    ]:
        log.debug("Found VAT in VA element: %s", vat)
        return vat

    # --- ESLOG RFF qualifiers ---
    vat_ahp = ""
    rffs = grp.findall(".//e:S_RFF", NS) + grp.findall(".//S_RFF")
    for rff in rffs:
        code_el = rff.find("./e:C_C506/e:D_1153", NS)
        if code_el is None:
            code_el = rff.find("./C_C506/D_1153")
        val_el = rff.find("./e:C_C506/e:D_1154", NS)
        if val_el is None:
            val_el = rff.find("./C_C506/D_1154")
        code = _text(code_el)
        val = _text(val_el)
        if code in VAT_QUALIFIERS and val:
            log.debug("Found VAT in RFF %s: %s", code, val)
            if code == "AHP":
                if not vat_ahp:
                    vat_ahp = val
            else:
                return val

    if vat_ahp:
        log.debug("Found VAT in RFF AHP: %s", vat_ahp)
        return vat_ahp

    log.debug("VAT element not found")
    return ""


# ────────────────────── dobavitelj: koda ──────────────────────
def get_supplier_info(tree: LET._ElementTree | LET._Element) -> str:
    """Return supplier code from a parsed XML tree.

    VAT numbers (``schemeID="VA"`` or a custom ``<VA>`` element) take
    precedence over other identifiers.  If no VAT information is present the
    helper falls back to GLN codes with ``schemeID="0088"`` and finally to any
    available supplier code from the NAD segment.  Debug output is emitted for
    the VAT lookup.  Returns ``"Unknown"`` when neither VAT nor GLN codes are
    found.
    """

    try:
        root = tree.getroot() if hasattr(tree, "getroot") else tree

        groups: List[LET._Element] = [
            sg2
            for sg2 in root.findall(".//e:G_SG2", NS)
            if _text(sg2.find("./e:S_NAD/e:D_3035", NS)) in {"SU", "SE"}
        ]
        if not groups:
            groups = [root]

        for grp in groups:
            # VAT takes precedence and can be present even without NAD segments
            code = _find_vat(grp)
            log.debug("Supplier VAT lookup result: %s", code or "not found")
            if code:
                return code

            nad = grp.find("./e:S_NAD", NS)
            if nad is None:
                nad = next(
                    (c for c in grp.iter() if c.tag.split("}")[-1] == "S_NAD"),
                    None,
                )

            if nad is not None:
                typ_el = nad.find("./e:D_3035", NS)
                if typ_el is None:
                    typ_el = next(
                        (
                            el
                            for el in nad.iter()
                            if el.tag.split("}")[-1] == "D_3035"
                        ),
                        None,
                    )
                typ = _text(typ_el)
                if typ not in {"SU", "SE"}:
                    continue

                code = _find_gln(nad)
                if not code:
                    code = _find_any_code(nad)
                if code:
                    return code
            else:
                # Fallback for UBL structures without NAD segments
                gln = [
                    v.strip()
                    for v in grp.xpath(".//*[@schemeID='0088']/text()")
                    if v.strip()
                ]
                if gln:
                    log.debug("Fallback to GLN: %s", gln[0])
                    return gln[0]
        log.debug("No VAT or GLN found")
    except Exception as exc:
        log.debug("Supplier code extraction failed: %s", exc)
    return "Unknown"


def get_supplier_name(xml_path: str | Path) -> Optional[str]:
    """Return supplier name if available."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        ns = {k: v for k, v in root.nsmap.items() if k}
        # UBL supplier name
        name = " ".join(
            n.strip()
            for n in root.xpath(
                ".//cac:PartyName/cbc:Name/text()", namespaces=ns
            )
            if n and n.strip()
        )
        if name:
            return name
        # eSLOG NAD segment
        name_els = root.xpath(
            ".//e:S_NAD/e:C_C080/e:D_3036/text()", namespaces=NS
        )
        if name_els:
            return " ".join(n.strip() for n in name_els if n.strip()) or None
    except Exception:
        pass
    return None


# ────────────────────── dobavitelj: koda + ime + davčna ──────────────────────
def get_supplier_info_vat(xml_path: str | Path) -> Tuple[str, str, str | None]:
    """Return supplier code, name and VAT number if available."""

    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
    except Exception:
        return "", "", None

    code = get_supplier_info(tree)
    vat_val = _find_vat(root) or None
    name = get_supplier_name(xml_path) or ""
    if vat_val:
        code = vat_val
    return code, name, vat_val


# ─────────────────────── vsota iz glave ───────────────────────
def extract_header_net(source: Path | str | Any) -> Decimal:
    """Return invoice net amount adjusted for document discounts/charges.

    The base net amount is looked up in header ``S_MOA`` segments using the
    common MOA codes ``203`` (line item amount), ``389`` (invoice amount) and
    ``79``.  After obtaining the base net value the function subtracts any
    document level discounts (``DEFAULT_DOC_DISCOUNT_CODES``) and adds document
    level charges (``DEFAULT_DOC_CHARGE_CODES``) using :func:`sum_moa`.
    """

    try:
        if hasattr(source, "findall"):
            root = source
        else:
            tree = LET.parse(source, parser=XML_PARSER)
            root = tree.getroot()

        header_base = Decimal("0")
        for code in ("203", "389", "79"):
            for moa in root.findall(".//e:G_SG50/e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == code:
                    header_base = _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                    break
            if header_base != 0:
                break

        line_base = Decimal("0")
        for seg in root.findall(".//e:G_SG26", NS) + root.findall(".//G_SG26"):
            for moa in seg.findall(".//e:S_MOA", NS) + seg.findall(".//S_MOA"):
                code_el = moa.find("./e:C_C516/e:D_5025", NS)
                if code_el is None:
                    code_el = moa.find("./C_C516/D_5025")
                if code_el is not None and _text(code_el) == "203":
                    val_el = moa.find("./e:C_C516/e:D_5004", NS)
                    if val_el is None:
                        val_el = moa.find("./C_C516/D_5004")
                    line_base += _decimal(val_el)

        doc_discount = sum_moa(
            root, DEFAULT_DOC_DISCOUNT_CODES, negative_only=True
        )
        doc_charge = sum_moa(root, DEFAULT_DOC_CHARGE_CODES)

        if line_base != 0:
            base = line_base
            line_adjusted = line_base - doc_discount + doc_charge
            if header_base != 0 and abs(header_base - line_adjusted) > DEC2:
                base = header_base
        else:
            base = header_base

        net = base - doc_discount + doc_charge
        if net < 0 and header_base > 0:
            net = header_base
        return net.quantize(DEC2, ROUND_HALF_UP)
    except Exception:
        pass
    return Decimal("0")


def extract_header_gross(xml_path: Path | str) -> Decimal:
    """Return gross amount from MOA 9 or 388."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        for code in ("9", "388"):
            for moa in root.findall(".//e:G_SG50/e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == code:
                    return _decimal(moa.find("./e:C_C516/e:D_5004", NS))
    except Exception:
        pass
    return Decimal("0")


def extract_grand_total(source: Path | str | Any) -> Decimal:
    """Return invoice grand total from MOA 9."""
    try:
        if hasattr(source, "findall"):
            root = source
        else:
            tree = LET.parse(source, parser=XML_PARSER)
            root = tree.getroot()
        for moa in root.findall(".//e:G_SG50/e:S_MOA", NS):
            if (
                _text(moa.find("./e:C_C516/e:D_5025", NS))
                == Moa.GRAND_TOTAL.value
            ):
                return _decimal(moa.find("./e:C_C516/e:D_5004", NS))
    except Exception:
        pass
    return Decimal("0")


def _tax_rate_from_header(root: LET._Element) -> Decimal:
    """Return default VAT rate from header ``S_TAX`` segment if present."""
    try:
        for tax in root.findall(".//e:G_SG16//e:S_TAX", NS):
            rate = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
            if rate != 0:
                return rate / Decimal("100")
        for tax in root.findall(".//G_SG16//S_TAX"):
            rate_el = tax.find("./C_C243/D_5278")
            if rate_el is not None:
                try:
                    rate = Decimal((rate_el.text or "0").replace(",", "."))
                    if rate != 0:
                        return rate / Decimal("100")
                except Exception:
                    continue
    except Exception:
        pass
    return Decimal("0")


def _invoice_total(
    header_net: Decimal,
    line_net_total: Decimal,
    doc_discount: Decimal,
    doc_charge: Decimal,
    tax_total: Decimal,
) -> Decimal:
    """Return invoice gross total."""

    if header_net != 0:
        net = header_net
    else:
        net = line_net_total - doc_discount

    return (net + doc_charge + tax_total).quantize(
        DEC2, rounding=ROUND_HALF_UP
    )


# ───────────────────── datum opravljene storitve ─────────────────────
def extract_service_date(xml_path: Path | str) -> str | None:
    """Vrne datum opravljene storitve (DTM 35) ali datum računa (DTM 137)."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        for dtm in root.findall(".//e:S_DTM", NS):
            if _text(dtm.find("./e:C_C507/e:D_2005", NS)) == "35":
                date = _text(dtm.find("./e:C_C507/e:D_2380", NS))
                if date:
                    return _normalize_date(date)
        for dtm in root.findall(".//e:S_DTM", NS):
            if _text(dtm.find("./e:C_C507/e:D_2005", NS)) == "137":
                date = _text(dtm.find("./e:C_C507/e:D_2380", NS))
                if date:
                    return _normalize_date(date)
    except Exception:
        pass
    return None


# ───────────────────── številka računa ─────────────────────
def extract_invoice_number(xml_path: Path | str) -> str | None:
    """Vrne številko računa iz segmenta BGM (D_1004)."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        bgm = root.find(".//e:S_BGM", NS)
        if bgm is None:
            for node in root.iter():
                if node.tag.split("}")[-1] == "S_BGM":
                    bgm = node
                    break
        if bgm is not None:
            num_el = bgm.find(".//e:C_C106/e:D_1004", NS)
            if num_el is None:
                num_el = next(
                    (
                        el
                        for el in bgm.iter()
                        if el.tag.split("}")[-1] == "D_1004"
                    ),
                    None,
                )
            if num_el is not None:
                num = _text(num_el)
                if num:
                    return num
    except Exception:
        pass
    return None


def extract_total_tax(xml_path: Path | str) -> Decimal:
    """Sum MOA values with qualifier 124 inside all ``G_SG52`` groups."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        total = Decimal("0")
        for sg52 in root.findall(".//e:G_SG52", NS):
            for moa in sg52.findall("./e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == "124":
                    total += _decimal(moa.find("./e:C_C516/e:D_5004", NS))
        return total.quantize(Decimal("0.01"), ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def sum_moa(
    root: LET._Element,
    codes: List[str],
    *,
    negative_only: bool = False,
    tax_amount: Decimal | None = None,
) -> Decimal:
    """Return the sum of MOA amounts for the given codes.

    Only ``S_MOA`` elements that appear within allowance/charge segments
    (``S_ALC``) are considered.  Segments nested inside tax summary
    groups (``G_SG52``) are ignored.  When ``negative_only`` is ``True``
    only negative amounts are summed and their absolute values returned.
    Amounts matching ``tax_amount`` are skipped to avoid mistaking VAT
    totals for discounts.
    """

    wanted = set(codes)
    total = Decimal("0")
    # Locate all allowance/charge segments and evaluate sibling MOA values
    alcs = root.findall(".//e:S_ALC", NS) + root.findall(".//S_ALC")
    for alc in alcs:
        # Skip allowances in tax summary groups
        parent = alc.getparent()
        ancestor = parent
        skip = False
        while ancestor is not None:
            if ancestor.tag.split("}")[-1] == "G_SG52":
                skip = True
                break
            ancestor = ancestor.getparent()
        if skip or parent is None:
            continue

        for moa in parent.findall("./e:S_MOA", NS) + parent.findall("./S_MOA"):
            code_el = moa.find("./e:C_C516/e:D_5025", NS)
            if code_el is None:
                code_el = moa.find("./C_C516/D_5025")
            if code_el is None or _text(code_el) not in wanted:
                continue

            val_el = moa.find("./e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = moa.find("./C_C516/D_5004")
            val = _decimal(val_el)

            if negative_only:
                if val >= 0:
                    continue
                if tax_amount is not None and abs(val) == abs(tax_amount):
                    continue
                total += -val
            else:
                total += val

    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _get_document_discount(xml_root: LET._Element) -> Decimal:
    """Return document level discount from <DocumentDiscount> or MOA codes."""
    discount_el = xml_root.find("DocumentDiscount")
    discount_str = discount_el.text if discount_el is not None else None

    def _find_moa_values(codes: set[str]) -> Decimal:
        total = Decimal("0")
        for seg in xml_root.iter():
            if seg.tag.split("}")[-1] != "S_MOA":
                continue
            code = None
            amount = None
            for el in seg.iter():
                tag = el.tag.split("}")[-1]
                if tag == "D_5025":
                    code = (el.text or "").strip()
                elif tag == "D_5004":
                    amount = (el.text or "").strip()
            if code in set(DEFAULT_DOC_DISCOUNT_CODES) and amount is not None:
                val = Decimal(amount.replace(",", "."))
                if val < 0:
                    total += -val
        return total

    discount = (
        Decimal(discount_str.replace(",", "."))
        if discount_str not in (None, "")
        else _find_moa_values(set(DEFAULT_DOC_DISCOUNT_CODES))
    )
    if discount < 0:
        discount = -discount

    return discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _line_discount(sg26: LET._Element) -> Decimal:
    """Return discount amount for a line (sum of MOA 204 values)."""
    total = Decimal("0")
    seen: set[tuple[int, str, Decimal]] = set()
    for moa in sg26.findall(".//e:S_MOA", NS) + sg26.findall(".//S_MOA"):
        code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
            moa.find("./C_C516/D_5025")
        )
        if code == Moa.DISCOUNT.value:
            amount_el = moa.find("./e:C_C516/e:D_5004", NS)
            if amount_el is None:
                amount_el = moa.find("./C_C516/D_5004")
            amount = _decimal(amount_el).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            key = (id(moa), code, amount)
            if key in seen:
                continue
            seen.add(key)
            total += amount

    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _line_amount_discount(sg26: LET._Element) -> Decimal:
    """Return sum of MOA 204 and 25 allowance amounts for a line."""

    total = Decimal("0")
    seen: set[tuple[int, str, Decimal]] = set()
    for sg39 in sg26.findall(".//e:G_SG39", NS) + sg26.findall(".//G_SG39"):
        for moa in sg39.findall(".//e:S_MOA", NS) + sg39.findall(".//S_MOA"):
            code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
                moa.find("./C_C516/D_5025")
            )
            if code in {Moa.DISCOUNT.value, "25"}:
                amount_el = moa.find("./e:C_C516/e:D_5004", NS)
                if amount_el is None:
                    amount_el = moa.find("./C_C516/D_5004")
                amount = _decimal(amount_el).quantize(DEC2, ROUND_HALF_UP)
                key = (id(moa), code, amount)
                if key in seen:
                    continue
                seen.add(key)
                total += amount

    return total.quantize(DEC2, ROUND_HALF_UP)


def _line_pct_discount(sg26: LET._Element) -> Decimal:
    """Return discount amount calculated from ``G_SG39`` percentage values."""
    total = Decimal("0")
    qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
    price_gross = Decimal("0")
    for pri in sg26.findall(".//e:S_PRI", NS) + sg26.findall(".//S_PRI"):
        code_el = pri.find("./e:C_C509/e:D_5125", NS)
        if code_el is None:
            code_el = pri.find("./C_C509/D_5125")
        if _text(code_el) == "AAB":
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price_gross = _decimal(val_el)
            break
    if price_gross == 0:
        for pri in sg26.findall(".//e:S_PRI", NS) + sg26.findall(".//S_PRI"):
            code_el = pri.find("./e:C_C509/e:D_5125", NS)
            if code_el is None:
                code_el = pri.find("./C_C509/D_5125")
            if _text(code_el) == "AAA":
                val_el = pri.find("./e:C_C509/e:D_5118", NS)
                if val_el is None:
                    val_el = pri.find("./C_C509/D_5118")
                price_gross = _decimal(val_el)
                break

    for sg39 in sg26.findall(".//e:G_SG39", NS) + sg26.findall(".//G_SG39"):
        code_el = sg39.find("./e:S_ALC/e:C_C552/e:D_5189", NS)
        if code_el is None:
            code_el = sg39.find("./S_ALC/C_C552/D_5189")
        if _text(code_el) != "95":
            continue
        qualifier_el = sg39.find("./e:G_SG41/e:S_PCD/e:C_C501/e:D_5249", NS)
        if qualifier_el is None:
            qualifier_el = sg39.find("./G_SG41/S_PCD/C_C501/D_5249")
        qualifier = _text(qualifier_el)
        if qualifier not in {"1", "2", "3"}:
            continue
        pct_el = sg39.find("./e:G_SG41/e:S_PCD/e:C_C501/e:D_5482", NS)
        if pct_el is None:
            pct_el = sg39.find("./G_SG41/S_PCD/C_C501/D_5482")
        pct = _decimal(pct_el)
        if pct == 0 or qty == 0:
            continue
        if qualifier == "1":
            total += price_gross * qty * pct / Decimal("100")
        elif qualifier == "2":
            total += price_gross * qty * (Decimal("1") - pct)
        else:  # qualifier == "3"
            total += pct

    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _line_gross(sg26: LET._Element) -> Decimal:
    """Return gross line amount including VAT.

    Tries ``PRI.AAA`` × ``QTY`` first, then ``PRI.AAB``.  If both prices are
    missing, the function falls back to ``MOA 38`` which contains the gross
    amount with VAT.
    """

    qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))

    price = Decimal("0")
    for pri in sg26.findall(".//e:S_PRI", NS) + sg26.findall(".//S_PRI"):
        code = _text(pri.find("./e:C_C509/e:D_5125", NS)) or _text(
            pri.find("./C_C509/D_5125")
        )
        if code == "AAA":
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price = _decimal(val_el)
            break
        if code == "AAB" and price == 0:
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price = _decimal(val_el)

    if price != 0 and qty != 0:
        return (price * qty).quantize(DEC2, ROUND_HALF_UP)

    for moa in sg26.findall(".//e:S_MOA", NS) + sg26.findall(".//S_MOA"):
        code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
            moa.find("./C_C516/D_5025")
        )
        if code == "38":
            val_el = moa.find("./e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = moa.find("./C_C516/D_5004")
            val = _decimal(val_el)
            if val:
                return val.quantize(DEC2, ROUND_HALF_UP)

    return Decimal("0")


def _line_net(sg26: LET._Element) -> Decimal:
    """Return net line amount without VAT or discounts."""

    for moa in sg26.findall(".//e:S_MOA", NS) + sg26.findall(".//S_MOA"):
        code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
            moa.find("./C_C516/D_5025")
        )
        if code in {"203", "125"}:
            val_el = moa.find("./e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = moa.find("./C_C516/D_5004")
            val = _decimal(val_el)
            if val:
                return val.quantize(DEC2, ROUND_HALF_UP)

    qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
    price_net = Decimal("0")
    price_gross = Decimal("0")
    for pri in sg26.findall(".//e:S_PRI", NS) + sg26.findall(".//S_PRI"):
        code = _text(pri.find("./e:C_C509/e:D_5125", NS)) or _text(
            pri.find("./C_C509/D_5125")
        )
        if code == "AAA":
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price_net = _decimal(val_el)
            break
        if code == "AAB" and price_gross == 0:
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price_gross = _decimal(val_el)

    if price_net == 0 and price_gross != 0:
        pct = Decimal("0")
        for sg39 in sg26.findall(".//e:G_SG39", NS) + sg26.findall(
            ".//G_SG39"
        ):
            qualifier = _text(
                sg39.find("./e:G_SG41/e:S_PCD/e:C_C501/e:D_5249", NS)
            ) or _text(sg39.find("./G_SG41/S_PCD/C_C501/D_5249"))
            if qualifier != "1":
                continue
            pct_el = sg39.find("./e:G_SG41/e:S_PCD/e:C_C501/e:D_5482", NS)
            if pct_el is None:
                pct_el = sg39.find("./G_SG41/S_PCD/C_C501/D_5482")
            pct = _decimal(pct_el)
            if pct != 0:
                break

        if pct != 0:
            price_net = (
                price_gross * (Decimal("1") - pct / Decimal("100"))
            ).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        else:
            price_net = price_gross
    elif price_net == 0:
        price_net = price_gross

    amount = (price_net * qty - _line_discount(sg26)).quantize(
        DEC2, ROUND_HALF_UP
    )
    return amount


def _line_tax(
    sg26: LET._Element, default_rate: Decimal | None = None
) -> Decimal:
    """Return VAT amount for a line."""
    abs_tax = Decimal("0")
    for moa in sg26.findall(".//e:G_SG34/e:S_MOA", NS) + sg26.findall(
        ".//S_MOA"
    ):
        code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
            moa.find("./C_C516/D_5025")
        )
        if code == "124":
            val_el = moa.find("./e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = moa.find("./C_C516/D_5004")
            abs_tax += _decimal(val_el)

    if abs_tax:
        return abs_tax.quantize(DEC2, ROUND_HALF_UP)

    rate = None
    for path in (".//e:G_SG34/e:S_TAX", ".//e:G_SG52/e:S_TAX"):
        for tax in sg26.findall(path, NS):
            r = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
            if r:
                rate = r / Decimal("100")
                break
        if rate is not None:
            break
    if rate is None and default_rate:
        rate = default_rate

    if rate:
        return (_line_net(sg26) * rate).quantize(DEC2, ROUND_HALF_UP)

    return Decimal("0.00")


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
    Če nobena vrstica ne vsebuje zneska DDV (MOA 124), se skupni DDV izračuna
    iz vsote neto postavk in stopnje DDV iz glave (če obstaja).
    Vrne tudi ``bool`` flag, ki označuje ali vsota ``net_total + tax_total``
    ustreza znesku iz segmenta ``MOA 9``.
    """
    supplier_code = ""

    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
    except EntitiesForbidden:
        return pd.DataFrame(), True
    root = tree.getroot()
    supplier_code = get_supplier_info(tree)
    header_rate = _tax_rate_from_header(root)
    header_net = extract_header_net(root)
    items: List[Dict] = []
    net_total = Decimal("0")
    tax_total = Decimal("0")
    vat_mismatch = False

    # ───────────── LINE ITEMS ─────────────
    for sg26 in root.findall(".//e:G_SG26", NS):
        qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
        if qty == 0:
            continue
        unit = _text(sg26.find(".//e:S_QTY/e:C_C186/e:D_6411", NS))
        item: Dict[str, Any] = {}

        # poiščemo šifro artikla
        art_code = ""
        lin_code = _text(sg26.find(".//e:S_LIN/e:C_C212/e:D_7140", NS))
        art_code = re.sub(r"\D+", "", lin_code)
        if not art_code:
            pia_first = sg26.find(".//e:S_PIA/e:C_C212/e:D_7140", NS)
            if pia_first is not None:
                art_code = re.sub(r"\D+", "", pia_first.text or "")

        desc = _text(sg26.find(".//e:S_IMD/e:C_C273/e:D_7008", NS))

        gross_amount = _line_gross(sg26)

        net_amount_moa: Decimal | None = None
        for moa in sg26.findall(".//e:S_MOA", NS):
            if _text(moa.find("./e:C_C516/e:D_5025", NS)) == Moa.NET.value:
                net_amount_moa = _decimal(
                    moa.find("./e:C_C516/e:D_5004", NS)
                ).quantize(Decimal("0.01"), ROUND_HALF_UP)
                break

        rebate_moa = _line_discount(sg26)
        pct_rebate = _line_pct_discount(sg26)
        rebate = rebate_moa + pct_rebate

        net_amount = _line_net(sg26)
        tax_amount = _line_tax(sg26, header_rate if header_rate != 0 else None)
        item["ddv"] = tax_amount if tax_amount is not None else Decimal("0")
        if net_amount == 0 and gross_amount != 0:
            net_amount = (gross_amount - rebate - tax_amount).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )

        if net_amount_moa is not None and net_amount != net_amount_moa:
            log.warning(
                "Line net mismatch: MOA 203 %s vs calculated %s",
                net_amount_moa,
                net_amount,
            )

        net_total = (net_total + net_amount).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        tax_total = (tax_total + tax_amount).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )

        # stopnja DDV (npr. 9.5 ali 22)
        vat_rate = Decimal("0")
        for tax in sg26.findall(".//e:G_SG34/e:S_TAX", NS):
            rate = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
            if rate != 0:
                vat_rate = rate
                break

        expected_tax = calculate_vat(net_amount, vat_rate)
        if expected_tax != tax_amount:
            log.error(
                "Line VAT mismatch: XML %s vs calculated %s (net %s rate %s)",
                tax_amount,
                expected_tax,
                net_amount,
                vat_rate,
            )
            vat_mismatch = True

        # rabat na ravni vrstice
        explicit_pct: Decimal | None = None
        for sg39 in sg26.findall(".//e:G_SG39", NS):
            if _text(sg39.find("./e:S_ALC/e:D_5463", NS)) != "A":
                continue
            pct = _decimal(sg39.find("./e:S_PCD/e:C_C501/e:D_5482", NS))
            if pct != 0:
                explicit_pct = pct.quantize(Decimal("0.01"), ROUND_HALF_UP)

        rebate = rebate.quantize(Decimal("0.01"), ROUND_HALF_UP)

        # izračun cen pred in po rabatu
        if qty:
            cena_pred = ((net_amount + rebate) / qty).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            cena_post = (net_amount / qty).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
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

        item.update(
            {
                "sifra_dobavitelja": supplier_code,
                "naziv": desc,
                "kolicina": qty,
                "enota": unit,
                "cena_bruto": cena_pred,
                "cena_netto": cena_post,
                "rabata": rebate,
                "rabata_pct": rabata_pct,
                "is_gratis": is_gratis,
                "vrednost": net_amount,
                "ddv_stopnja": vat_rate,
                "sifra_artikla": art_code,
            }
        )

        if "ddv" not in item:
            item["ddv"] = Decimal("0")

        items.append(item)

        for ac in sg26.findall(".//e:AllowanceCharge", NS) + sg26.findall(
            ".//AllowanceCharge"
        ):
            ind_el = ac.find("./e:ChargeIndicator", NS)
            if ind_el is None:
                ind_el = ac.find("./ChargeIndicator")
            indicator = _text(ind_el).lower()

            amt_el = ac.find("./e:Amount", NS)
            if amt_el is None:
                amt_el = ac.find("./Amount")
            amount = _decimal(amt_el)
            if indicator in {"true", "1"} and amount > 0:
                desc_el = ac.find("./e:AllowanceChargeReason", NS)
                if desc_el is None:
                    desc_el = ac.find("./AllowanceChargeReason")
                desc_ac = _text(desc_el)

                code_el = ac.find("./e:AllowanceChargeReasonCode", NS)
                if code_el is None:
                    code_el = ac.find("./AllowanceChargeReasonCode")
                code_ac = _text(code_el) or "_CHARGE_"

                rate_el = ac.find(
                    ".//e:TaxCategory/e:Percent",
                    {**NS, **UBL_NS},
                )
                if rate_el is None:
                    rate_el = ac.find(
                        ".//cac:TaxCategory/cbc:Percent",
                        {**NS, **UBL_NS},
                    )
                    if rate_el is None:
                        log.warning(
                            "Tax rate element not found with namespaces; "
                            "falling back",
                        )
                        rate_el = ac.find(".//TaxCategory/Percent")
                vat_rate = _decimal(rate_el)

                tax_el = ac.find(
                    ".//cac:TaxTotal/cbc:TaxAmount", {**NS, **UBL_NS}
                )
                if tax_el is not None:
                    log.debug("cbc:TaxAmount raw value: %s", tax_el.text)
                else:
                    log.warning("Missing .//cbc:TaxAmount; falling back")
                    tax_el = ac.find(
                        ".//e:TaxTotal/e:TaxAmount",
                        {**NS, **UBL_NS},
                    ) or ac.find(".//TaxTotal/TaxAmount")
                vat_amount = _decimal(tax_el)
                if vat_amount == 0 and vat_rate != 0:
                    vat_amount = calculate_vat(amount, vat_rate)

                expected_vat = calculate_vat(amount, vat_rate)
                if vat_amount != expected_vat:
                    log.error(
                        "Allowance/charge VAT mismatch: XML %s vs "
                        "calculated %s (net %s rate %s)",
                        vat_amount,
                        expected_vat,
                        amount,
                        vat_rate,
                    )
                    vat_mismatch = True

                net_total = (net_total + amount).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
                tax_total = (tax_total + vat_amount).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
                items.append(
                    {
                        "sifra_dobavitelja": code_ac,
                        "naziv": desc_ac,
                        "kolicina": Decimal("1"),
                        "enota": "",
                        "cena_bruto": amount,
                        "cena_netto": amount,
                        "rabata": Decimal("0"),
                        "rabata_pct": Decimal("0.00"),
                        "vrednost": amount,
                        "ddv_stopnja": vat_rate,
                        "ddv": vat_amount,
                        "is_gratis": False,
                    }
                )

    # ───────── DOCUMENT ALLOWANCES & CHARGES ─────────
    doc_discount = sum_moa(
        root,
        discount_codes or DEFAULT_DOC_DISCOUNT_CODES,
        negative_only=True,
        tax_amount=tax_total,
    )
    doc_charge = sum_moa(root, DEFAULT_DOC_CHARGE_CODES)

    if doc_discount != 0:
        items.append(
            {
                "sifra_dobavitelja": "_DOC_",
                "naziv": "Popust na ravni računa",
                "kolicina": Decimal("1"),
                "enota": "",
                "cena_bruto": -doc_discount,
                "cena_netto": -doc_discount,
                "rabata": doc_discount,
                "rabata_pct": Decimal("100.00"),
                "vrednost": -doc_discount,
                "ddv": Decimal("0"),
                "is_gratis": False,
            }
        )

    if doc_charge != 0:
        items.append(
            {
                "sifra_dobavitelja": "DOC_CHG",
                "naziv": "Strošek na ravni računa",
                "kolicina": Decimal("1"),
                "enota": "",
                "cena_bruto": doc_charge,
                "cena_netto": doc_charge,
                "rabata": Decimal("0"),
                "rabata_pct": Decimal("0.00"),
                "vrednost": doc_charge,
                "ddv": Decimal("0"),
                "is_gratis": False,
            }
        )

    net_total = (net_total - doc_discount + doc_charge).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )

    if tax_total == 0 and header_rate != 0:
        tax_total = (net_total * header_rate).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )

    df = pd.DataFrame(items)
    df.attrs["vat_mismatch"] = vat_mismatch
    if "sifra_dobavitelja" in df.columns and not df["sifra_dobavitelja"].any():
        df["sifra_dobavitelja"] = supplier_code
    if not df.empty:
        df.sort_values(
            ["sifra_dobavitelja", "naziv"], inplace=True, ignore_index=True
        )

    calculated_total = _invoice_total(
        header_net, net_total, Decimal("0"), Decimal("0"), tax_total
    )
    grand_total = extract_grand_total(xml_path)
    ok = True
    if grand_total != 0:
        ok = abs(calculated_total - grand_total) <= Decimal("0.01")
        if not ok:
            log.warning(
                "Invoice total mismatch: MOA 9 %s vs calculated %s",
                grand_total,
                calculated_total,
            )

    return df, ok


# ───────────────────── PRILAGOJENA funkcija za CLI ─────────────────────
def parse_invoice(source: str | Path):
    """
    Parsira e-račun (ESLOG INVOIC) iz XML ali PDF (če je implementirano).
    Vrne:
      • df: DataFrame s stolpci ['cena_netto', 'kolicina', 'rabata_pct',
        'izracunana_vrednost'] (vrednosti so Decimal v object stolpcih)
      • header_total: Decimal (InvoiceTotal –
        DocumentDiscount + DocumentCharge)
      • discount_total: Decimal (znesek dokumentarnega popusta)
      • gross_total: Decimal (MOA 9 – skupni znesek računa)
    Uporablja se v CLI (wsm/cli.py).
    """
    # naložimo XML
    xml_source = source
    parsed_from_string = False
    if isinstance(source, (str, Path)) and Path(source).exists():
        tree = LET.parse(source, parser=XML_PARSER)
        root = tree.getroot()
    else:
        parsed_from_string = True
        root = LET.fromstring(source, parser=XML_PARSER)
        xml_source = io.BytesIO(source.encode())

    # Ali je pravi eSLOG (urn:eslog:2.00)?
    if (
        root.tag.endswith("Invoice")
        and root.find(".//e:M_INVOIC", NS) is not None
    ):
        df_items, ok = parse_eslog_invoice(xml_source)
        header_total = extract_header_net(
            root if parsed_from_string else xml_source
        )
        gross_total = extract_grand_total(
            root if parsed_from_string else xml_source
        )
        # ─────────────────────── dokumentarni popusti ───────────────────────
        # V vrstici "_DOC_" se lahko pojavijo negativne vrednosti (allowance)
        # in pozitivne (charge).  Popust naj vključuje le negativne zneske,
        # zato najprej filtriramo le tiste "_DOC_" vrstice,
        # katerih ``vrednost`` je manjša od 0.  Vrstice "DOC_CHG"
        # (dokumentarni stroški) so tako že privzeto izključene.
        doc_rows = df_items[
            (df_items["sifra_dobavitelja"] == "_DOC_")
            & (df_items["vrednost"] < 0)
        ]
        if not doc_rows.empty:
            allow_total = doc_rows["vrednost"].sum()
            discount_total = (-Decimal(allow_total)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            discount_total = sum_moa(
                root, DEFAULT_DOC_DISCOUNT_CODES, negative_only=True
            )

        # Če želimo posebej slediti tudi pribitkom:
        # charge_total = doc_rows.loc[
        #     doc_rows["vrednost"] > 0, "vrednost"
        # ].sum()
        df = pd.DataFrame(
            {
                "cena_netto": df_items["cena_netto"],
                "kolicina": df_items["kolicina"],
                "rabata_pct": df_items["rabata_pct"],
                "izracunana_vrednost": df_items["vrednost"],
            },
            dtype=object,
        )
        return df, header_total, discount_total, gross_total

    # Preprost <Racun> format z elementi <Postavka>
    if root.tag == "Racun" or root.find("Postavka") is not None:
        header_total = extract_total_amount(root)
        discount_total = _get_document_discount(root)
        gross_total = Decimal("0")
        rows = []
        for line in root.findall("Postavka"):
            name = line.findtext("Naziv") or ""
            qty_str = line.findtext("Kolicina") or "0"
            price_str = line.findtext("Cena") or "0"
            unit = line.attrib.get("enota", "").strip().lower()
            if not unit:
                name_l = name.lower()
                if re.search(r"\bkos\b", name_l):
                    unit = "kos"
                elif re.search(r"\bkg\b", name_l):
                    unit = "kg"
            price = Decimal(price_str.replace(",", "."))
            qty = Decimal(qty_str.replace(",", "."))
            izracun_val = (price * qty).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            rows.append(
                {
                    "cena_netto": price,
                    "kolicina": qty,
                    "rabata_pct": Decimal("0"),
                    "izracunana_vrednost": izracun_val,
                    "enota": unit,
                    "naziv": name,
                }
            )
        df = pd.DataFrame(rows, dtype=object)
        return df, header_total, discount_total, gross_total

    # izvzamemo glavo (InvoiceTotal – DocumentDiscount + DocumentCharge)
    header_total = extract_total_amount(root)
    discount_total = _get_document_discount(root)
    gross_total = Decimal("0")

    # preberemo vse <LineItems/LineItem>
    rows = []

    for li in root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        kolic = Decimal(qty_str.replace(",", "."))

        # Some suppliers provide the final line value in MOA 203.  If present,
        # use it and derive the unit price from quantity.
        net_el = None
        for moa in li.findall(".//S_MOA"):
            code = _text(moa.find("./C_C516/D_5025"))
            if code == "203":
                candidate = moa.find("./C_C516/D_5004")
                if candidate is not None and _text(candidate):
                    net_el = candidate
                    break

        if net_el is not None:
            izracun_val = _decimal(net_el)
            if kolic != 0:
                cena = (izracun_val / kolic).quantize(
                    Decimal("0.0001"), ROUND_HALF_UP
                )
            else:
                cena = Decimal("0")
            rabata_pct = Decimal("0")
        else:
            cena = Decimal(price_str.replace(",", "."))
            rabata_pct = Decimal(discount_pct_str.replace(",", "."))
            izracun_val = (
                cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))
            ).quantize(Decimal("0.01"), ROUND_HALF_UP)

        rows.append(
            {
                "cena_netto": cena,
                "kolicina": kolic,
                "rabata_pct": rabata_pct,
                "izracunana_vrednost": izracun_val,
            }
        )

    # Če ni nobenih vrstic, naredimo prazen DataFrame z ustreznimi stolpci
    if not rows:
        df = pd.DataFrame(
            columns=[
                "cena_netto",
                "kolicina",
                "rabata_pct",
                "izracunana_vrednost",
            ]
        )
    else:
        df = pd.DataFrame(rows, dtype=object)

    return df, header_total, discount_total, gross_total


def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri vsoto vseh izracunana_vrednost (Decimal) proti header_total.
    Če stolpec ne obstaja (težak primer z nenavadno strukturo XML), vrne False.
    """
    # Preverimo, ali je stolpec sploh prisoten
    if "izracunana_vrednost" not in df.columns:
        return False

    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(
        lambda x: Decimal(str(x))
    )
    return validate_line_values(df, header_total)
