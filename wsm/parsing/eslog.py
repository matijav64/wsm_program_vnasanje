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
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from types import SimpleNamespace

import pandas as pd
from defusedxml.common import EntitiesForbidden
from lxml import etree
from lxml import etree as LET

from .codes import Moa
from .utils import _normalize_date
from wsm.parsing.money import (
    extract_total_amount,
    validate_invoice as validate_line_values,
    calculate_vat,
)

# --- dynamic namespace mapping for 'e:' ---
NS = {
    "e": "urn:edifact:xml:enriched"
}  # default; will be overridden per document


def _detect_edifact_ns(root) -> str:
    """Return namespace URI present in the document."""
    try:
        tag = getattr(root, "tag", "")
        if isinstance(tag, str) and tag.startswith("{"):
            uri = tag.split("}", 1)[0][1:]
            if uri:
                return uri
        for el in root.iter():
            t = getattr(el, "tag", "")
            if isinstance(t, str) and t.startswith("{"):
                return t.split("}", 1)[0][1:]
    except Exception:
        pass
    return "urn:edifact:xml:enriched"


def _force_ns_for_doc(root) -> None:
    """Align ``NS['e']`` with the namespace used in ``root``."""
    uri = _detect_edifact_ns(root)
    if uri in ("urn:edifact:xml:enriched", "urn:eslog:2.00"):
        NS["e"] = uri
    else:
        NS["e"] = "urn:edifact:xml:enriched"


XML_PARSER = LET.XMLParser(resolve_entities=False)

# Use higher precision to avoid premature rounding when summing values.
decimal.getcontext().prec = 28  # Python's default precision
DEC2 = Decimal("0.01")
DEC4 = Decimal("0.0001")
TOL = Decimal("0.01")
NET_TOL = Decimal("0.10")


def _first_text(root, xpaths: list[str]) -> str | None:
    """
    Vrne ``.text`` prve ujemajoče se poti. Podpira 'es' (eSLOG 2.00) in 'e'
    (enriched EDIFACT), ter fallback z ``local-name()``.
    """
    ns_default = {"e": "urn:edifact:xml:enriched", "es": "urn:eslog:2.00"}
    ns = getattr(root, "nsmap", None) or ns_default

    for xp in xpaths:
        try:
            nodes = root.xpath(xp, namespaces=ns)
            if nodes:
                el = nodes[0]
                txt = (
                    el.text if isinstance(el, etree._Element) else str(el)
                ) or ""
                txt = txt.strip()
                if txt:
                    return txt
        except Exception:
            pass

    for xp in xpaths:
        parts = [p for p in xp.split("/") if p]
        loc = (
            ".//*["
            + " and ".join(
                f"local-name()='{p.split(':')[-1].split('[')[0]}'"
                for p in parts
                if p not in (".", "..")
            )
            + "]"
        )
        try:
            nodes = root.xpath(loc)
            if nodes:
                txt = (nodes[0].text or "").strip()
                if txt:
                    return txt
        except Exception:
            pass
    return None


# MOA qualifiers used for discounts and base amounts
DISCOUNT_MOA_LINE = {"204"}
DOC_DISCOUNT_MOA = {"260", "204"}
BASE_MOA_LINE = {"25"}

# Global flag indicating that SG26 discounts are informational only.
_INFO_DISCOUNTS = False

# module logger
log = logging.getLogger(__name__)
TRACE = os.getenv("WSM_TRACE", "0") not in {"0", "false", "False"}


def _t(msg, *args):
    if TRACE:
        log.warning("[TRACE PARSE] " + msg, *args)


# ────────────────────────── pomožne funkcije ──────────────────────────
def _dec2(x: Decimal) -> Decimal:
    """Quantize value to two decimal places using ``ROUND_HALF_UP``."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _moa_value(m: LET._Element) -> Decimal:
    """Extract signed monetary amount from an ``S_MOA`` element."""
    val_el = m.find("./e:C_C516/e:D_5004", NS)
    if val_el is None:
        val_el = m.find("./C_C516/D_5004")
    return _decimal(val_el)


def _sum_moa(node: LET._Element, codes: set[str], *, deep: bool) -> Decimal:
    total = Decimal("0")
    path = ".//e:S_MOA" if deep else "./e:S_MOA"
    path_alt = ".//S_MOA" if deep else "./S_MOA"
    nodes = node.findall(path, NS)
    nodes.extend(m for m in node.findall(path_alt) if m not in nodes)
    seen: set[str] = set()
    for m in nodes:
        q = m.find("e:C_C516/e:D_5025", NS)
        if q is None:
            q = m.find("C_C516/D_5025")
        qualifier = (q.text or "").strip() if q is not None else ""
        if qualifier in codes and qualifier not in seen:
            val_el = m.find("e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = m.find("C_C516/D_5004")
            total += _decimal(val_el)
            seen.add(qualifier)
    return total


def _line_moa203(sg26: LET._Element) -> Decimal:
    """Return MOA 203 value for a line from direct ``G_SG27/S_MOA``
    children."""
    for sg27 in sg26.findall("./e:G_SG27", NS) + sg26.findall("./G_SG27"):
        for cand in sg27.findall("./e:S_MOA", NS) + sg27.findall("./S_MOA"):
            q = cand.find("e:C_C516/e:D_5025", NS)
            if q is None:
                q = cand.find("C_C516/D_5025")
            if q is not None and _text(q) == "203":
                return _dec2(_moa_value(cand))
    for cand in sg26.findall("./e:S_MOA", NS) + sg26.findall("./S_MOA"):
        q = cand.find("e:C_C516/e:D_5025", NS)
        if q is None:
            q = cand.find("C_C516/D_5025")
        if q is not None and _text(q) == "203":
            return _dec2(_moa_value(cand))
    return Decimal("0.00")


def _get_pcd_shallow(node: LET._Element) -> list[Decimal]:
    """Return list of percentages from direct ``S_PCD`` children."""
    out: list[Decimal] = []
    for p in (
        node.findall("./e:S_PCD", NS)
        + node.findall("./S_PCD")
        + node.findall("./e:G_SG41/e:S_PCD", NS)
        + node.findall("./G_SG41/S_PCD")
    ):
        val = p.find("./e:C_C501/e:D_5482", NS)
        if val is None:
            val = p.find("./C_C501/D_5482")
        if val is not None and (val.text or "").strip():
            try:
                out.append(Decimal(val.text.strip()))
            except Exception:
                pass
    return out


def _iter_sg39(node: LET._Element):
    """Yield SG39 segments: (sg39_node, kind, pcd_list,
    moa_allow, moa_charge)."""
    for sg39 in node.findall("./e:G_SG39", NS) + node.findall("./G_SG39"):
        alc = sg39.find("./e:S_ALC/e:D_5463", NS)
        if alc is None:
            alc = sg39.find("./S_ALC/D_5463")
        kind = (alc.text or "").strip() if alc is not None else ""
        if kind not in {"A", "C"}:
            continue
        pcds = _get_pcd_shallow(sg39)
        if kind == "A":
            moa_allow = _sum_moa(sg39, DISCOUNT_MOA_LINE, deep=False)
            moa_charge = Decimal("0")
        else:
            moa_allow = Decimal("0")
            moa_charge = _sum_moa(sg39, DISCOUNT_MOA_LINE, deep=False)
        yield sg39, kind, pcds, moa_allow, moa_charge


def _first_moa(
    node: LET._Element, codes: set[str], *, ignore_sg26: bool = False
) -> Decimal:
    for m in node.findall(".//e:S_MOA", NS) + node.findall(".//S_MOA"):
        if ignore_sg26:
            anc = m.getparent()
            skip = False
            while anc is not None:
                if anc.tag.split("}")[-1] == "G_SG26":
                    skip = True
                    break
                anc = anc.getparent()
            if skip:
                continue
        q = m.find("e:C_C516/e:D_5025", NS)
        if q is None:
            q = m.find("C_C516/D_5025")
        if q is not None and (q.text or "").strip() in codes:
            val = _moa_value(m)
            if val:
                return val
    return Decimal("0")


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
DEFAULT_DOC_DISCOUNT_CODES = ["204", "260", "131", "128", "176", "500"]

# Common document charge codes.  These represent additional amounts
# that increase the invoice total.
DEFAULT_DOC_CHARGE_CODES = ["504"]

# Qualifiers used for seller VAT identification in ``S_RFF`` segments.
VAT_QUALIFIERS = {"VA", "0199", "AHP"}

# VAT pattern for Slovenian numbers and normalizer
VAT_ID_RE = re.compile(r"^SI\d{8}$")


def _normalize_vat_id(val: str) -> str:
    """Return VAT ID without spaces and uppercased."""
    return re.sub(r"\s+", "", val or "").upper()


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
                vat = _normalize_vat_id(vat)
                if VAT_ID_RE.match(vat):
                    log.debug("Found VAT in UBL element %s: %s", path, vat)
                    return vat

    # --- Custom <VA> element without schemeID ---
    for vat in [
        v.strip()
        for v in grp.xpath(".//*[local-name()='VA']/text()")
        if v.strip()
    ]:
        vat = _normalize_vat_id(vat)
        if VAT_ID_RE.match(vat):
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
            val = _normalize_vat_id(val)
            if VAT_ID_RE.match(val):
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
        _force_ns_for_doc(root)

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

    _force_ns_for_doc(root)

    code = get_supplier_info(tree)

    vat_val: str | None = None
    try:
        groups = []
        for grp in root.findall(".//e:G_SG2", NS):
            nad = grp.find("./e:S_NAD", NS)
            if nad is None:
                continue
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
            if typ in {"SU", "SE"}:
                priority = 0 if typ == "SU" else 1
                groups.append((priority, grp))
        groups.sort(key=lambda item: item[0])
        search_groups = [grp for _, grp in groups] or [root]
    except Exception:
        search_groups = [root]

    for grp in search_groups:
        vat_candidate = _find_vat(grp)
        if vat_candidate:
            vat_val = vat_candidate
            break

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
        _force_ns_for_doc(root)

        header_base = Decimal("0")
        header_base_code: str | None = None
        header_candidates: list[tuple[str, Decimal]] = []
        seen_header_codes: set[str] = set()
        for code in ("203", "389", "79", "125"):
            value = Decimal("0")
            for moa in root.findall(".//e:G_SG50/e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == code:
                    value = _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                    break
            if value != 0 and code not in seen_header_codes:
                header_candidates.append((code, value))
                seen_header_codes.add(code)
                if header_base == 0:
                    header_base = value
                    header_base_code = code

        summary_taxable = Decimal("0")
        for sg52 in root.findall(".//e:G_SG52", NS) + root.findall(".//G_SG52"):
            for moa in sg52.findall("./e:S_MOA", NS) + sg52.findall("./S_MOA"):
                code_el = moa.find("./e:C_C516/e:D_5025", NS)
                if code_el is None:
                    code_el = moa.find("./C_C516/D_5025")
                if _text(code_el) != "125":
                    continue
                val_el = moa.find("./e:C_C516/e:D_5004", NS)
                if val_el is None:
                    val_el = moa.find("./C_C516/D_5004")
                summary_taxable += _decimal(val_el)

        summary_taxable = _dec2(summary_taxable) if summary_taxable != 0 else Decimal("0")
        if summary_taxable != 0:
            if "125" not in seen_header_codes:
                header_candidates.append(("125", summary_taxable))
                seen_header_codes.add("125")
            if header_base == 0:
                header_base = summary_taxable

        header_gross = Decimal("0")
        for gross_code in ("9", "388"):
            gross_val = Decimal("0")
            for moa in root.findall(".//e:G_SG50/e:S_MOA", NS):
                if _text(moa.find("./e:C_C516/e:D_5025", NS)) == gross_code:
                    gross_val = _decimal(moa.find("./e:C_C516/e:D_5004", NS))
                    break
            if gross_val != 0:
                header_gross = gross_val
                break


        line_base = Decimal("0")
        line_doc_discount = Decimal("0")
        for seg in root.findall(".//e:G_SG26", NS) + root.findall(".//G_SG26"):
            base203 = sum(
                _sum_moa(sg27, {"203"}, deep=False)
                for sg27 in seg.findall("./e:G_SG27", NS)
                + seg.findall("./G_SG27")
            )
            doc_disc = _doc_discount_from_line(seg)
            if doc_disc is not None and base203 == 0:
                line_doc_discount += doc_disc
            for sg27 in seg.findall("./e:G_SG27", NS) + seg.findall(
                "./G_SG27"
            ):
                for moa in sg27.findall("./e:S_MOA", NS) + sg27.findall(
                    "./S_MOA"
                ):
                    code_el = moa.find("./e:C_C516/e:D_5025", NS)
                    if code_el is None:
                        code_el = moa.find("./C_C516/D_5025")
                    if _text(code_el) == "203":
                        val_el = moa.find("./e:C_C516/e:D_5004", NS)
                        if val_el is None:
                            val_el = moa.find("./C_C516/D_5004")
                        line_base += _decimal(val_el)
                        break
                else:
                    continue
                break

        tax_total = Decimal("0")
        for sg52 in root.findall(".//e:G_SG52", NS) + root.findall(
            ".//G_SG52"
        ):
            for moa in sg52.findall("./e:S_MOA", NS) + sg52.findall("./S_MOA"):
                code_el = moa.find("./e:C_C516/e:D_5025", NS)
                if code_el is None:
                    code_el = moa.find("./C_C516/D_5025")
                if _text(code_el) == "124":
                    val_el = moa.find("./e:C_C516/e:D_5004", NS)
                    if val_el is None:
                        val_el = moa.find("./C_C516/D_5004")
                    tax_total += _decimal(val_el)
        tax_total = tax_total.quantize(DEC2, ROUND_HALF_UP)

        doc_discount_header = sum_moa(
            root,
            DEFAULT_DOC_DISCOUNT_CODES,
            tax_amount=tax_total,
        )
        doc_charge = sum_moa(root, DEFAULT_DOC_CHARGE_CODES)
        doc_discount = (
            doc_discount_header
            if doc_discount_header != 0
            else line_doc_discount
        ).quantize(DEC2, ROUND_HALF_UP)

        selected_candidate_code: str | None = None
        adjustments_included = False
        line_adjusted: Decimal | None = None
        line_adjusted_q: Decimal | None = None

        if line_base != 0:
            base = line_base
            line_adjusted = line_base + doc_discount + doc_charge
            line_adjusted_q = line_adjusted.quantize(DEC2, ROUND_HALF_UP)
            if header_candidates:
                best_value: Decimal | None = None
                best_diff: Decimal | None = None

                if header_gross != 0:
                    scores: list[
                        tuple[Decimal, Decimal, Decimal, str, Decimal]
                    ] = []
                    for cand_code, value in header_candidates:
                        adjusted_net = value + doc_discount + doc_charge
                        gross_estimate = (adjusted_net + tax_total).quantize(
                            DEC2, ROUND_HALF_UP
                        )
                        gross_diff = abs(gross_estimate - header_gross)
                        line_diff = abs(value - line_adjusted_q)
                        header_diff = (
                            abs(value - header_base)
                            if header_base != 0
                            else line_diff
                        )
                        scores.append(
                            (gross_diff, line_diff, header_diff, cand_code, value)
                        )

                    within_tol = [s for s in scores if s[0] <= DEC2]
                    if within_tol:
                        gross_diff, line_diff, _, cand_code, cand_val = min(
                            within_tol, key=lambda s: (s[0], s[1], s[2])
                        )
                        best_value = cand_val
                        best_diff = line_diff
                        selected_candidate_code = cand_code

                if best_value is None:
                    cand_code, value, diff = min(
                        (
                            (
                                cand_code,
                                value,
                                abs(value - line_adjusted_q),
                            )
                            for cand_code, value in header_candidates
                        ),
                        key=lambda item: item[2],
                    )
                    best_value = value
                    best_diff = diff
                    selected_candidate_code = cand_code

                if best_diff is not None and best_diff <= DEC2:
                    base = best_value
                else:
                    selected_candidate_code = None
                    if (
                        header_base != 0
                        and abs(header_base - line_adjusted_q) > DEC2
                    ):
                        base = header_base
            elif header_base != 0 and abs(header_base - line_adjusted) > DEC2:
                base = header_base
        else:
            base = header_base
            selected_candidate_code = header_base_code

        if line_adjusted is not None and abs(base - line_adjusted) <= DEC4:
            adjustments_included = True
        elif line_adjusted is None and selected_candidate_code == "125":
            adjustments_included = True

        if adjustments_included:
            net = base
        else:
            net = base + doc_discount + doc_charge

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

    net = header_net if header_net != 0 else line_net_total
    net -= doc_discount
    net += doc_charge
    return (net + tax_total).quantize(DEC2, ROUND_HALF_UP)


def _apply_doc_allowances_sequential(
    sum_line_net: Decimal,
    header_node: LET._Element,
    *,
    discount_codes: set[str] | None = None,
    charge_codes: set[str] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Apply document-level allowances/charges sequentially."""
    base = sum_line_net
    run = base
    allow_total = Decimal("0")
    charge_total = Decimal("0")
    codes = set(discount_codes or DOC_DISCOUNT_MOA) | set(
        charge_codes or DEFAULT_DOC_CHARGE_CODES
    )
    for sg in header_node.findall(".//e:G_SG50", NS) + header_node.findall(
        ".//G_SG50"
    ):
        if sg.find("./e:S_ALC", NS) is None and sg.find("./S_ALC") is None:
            continue
        ancestor = sg.getparent()
        in_summary = False
        while ancestor is not None:
            if ancestor.tag.split("}")[-1] == "G_SG52":
                in_summary = True
                break
            ancestor = ancestor.getparent()
        if in_summary and _sum_moa(sg, codes, deep=False) == 0:
            continue
        alc = sg.find("./e:S_ALC/e:D_5463", NS)
        if alc is None:
            alc = sg.find("./S_ALC/D_5463")
        kind = (alc.text or "").strip() if alc is not None else ""
        if kind not in {"A", "C"}:
            continue
        for pct in _get_pcd_shallow(sg):
            amt = _dec2(run * pct / Decimal("100"))
            if kind == "A":
                amt = -amt
                allow_total += amt
            else:
                charge_total += amt
            run += amt
        moa = _sum_moa(sg, codes, deep=False)
        run += moa
        if kind == "A":
            allow_total += moa
        else:
            charge_total += moa
        if base >= 0 and run < 0:
            run = Decimal("0")
        elif base < 0 and run > 0:
            run = Decimal("0")
    return _dec2(run), _dec2(allow_total), _dec2(charge_total)


def _vat_total_after_doc(
    sum_tax_124: Decimal | None,
    lines_by_rate: dict[Decimal, Decimal],
    doc_allow_total: Decimal,
) -> Decimal:
    """Compute VAT total after document allowance allocation.

    ``sum_tax_124`` represents the sum of MOA 124 amounts already
    present in the document, either on individual lines or in the VAT
    summary.  When provided, these authoritative totals are returned
    directly.  Only when such MOA 124 values are missing do we prorate
    the document level discount across ``lines_by_rate`` and recompute
    the VAT on the reduced bases.
    """
    if sum_tax_124 is not None and sum_tax_124 != 0:
        return _dec2(sum_tax_124)
    if not lines_by_rate:
        return Decimal("0.00")
    base_total = sum(lines_by_rate.values())
    alloc = {
        rate: (
            _dec2((val / base_total) * doc_allow_total)
            if base_total
            else Decimal("0")
        )
        for rate, val in lines_by_rate.items()
    }
    vat = Decimal("0")
    for rate, base in lines_by_rate.items():
        eff_base = base - alloc.get(rate, Decimal("0"))
        if (base >= 0 and eff_base < 0) or (base < 0 and eff_base > 0):
            eff_base = Decimal("0")
        vat += _dec2(eff_base * rate / Decimal("100"))
    return _dec2(vat)


# ───────────────────── vrsta računa ─────────────────────
def extract_invoice_type(source: Path | str | Any) -> str:
    """Return the invoice type code if available.

    The helper inspects UBL ``cbc:InvoiceTypeCode`` elements and
    EDIFACT ``D_1001`` values inside ``S_BGM`` segments.  When the
    type cannot be determined an empty string is returned.
    """

    try:
        if hasattr(source, "find"):
            root = source
        else:
            tree = LET.parse(source, parser=XML_PARSER)
            root = tree.getroot()

        # --- UBL InvoiceTypeCode ---
        try:
            itc = root.find(".//cbc:InvoiceTypeCode", UBL_NS)
        except Exception:
            itc = None
        if itc is not None:
            val = _text(itc)
            if val:
                return val

        # --- EDIFACT D_1001 in BGM ---
        path_ns = ".//e:S_BGM/e:C_C002/e:D_1001"
        path_no = ".//S_BGM/C_C002/D_1001"
        el = root.find(path_ns, NS) or root.find(path_no)
        if el is None:
            el = next(
                (
                    node
                    for node in root.iter()
                    if node.tag.split("}")[-1] == "D_1001"
                ),
                None,
            )
        return _text(el)
    except Exception:
        return ""


# ───────────────────── datum opravljene storitve ─────────────────────
def extract_service_date(xml_path: Path | str) -> str | None:
    """Vrne datum opravljene storitve (DTM 35) ali datum računa (DTM 137)."""

    def _dtm_value(dtm: LET._Element, field: str) -> str:
        """Return ``C_C507`` child text regardless of namespaces."""

        try:
            value = dtm.xpath(
                f"string(./*[local-name()='C_C507']/*[local-name()='{field}'])"
            )
        except Exception:
            return ""
        return value.strip()

    def _find_date(nodes: list[LET._Element], qualifier: str) -> str | None:
        for dtm in nodes:
            if _dtm_value(dtm, "D_2005") == qualifier:
                date = _dtm_value(dtm, "D_2380")
                if date:
                    return _normalize_date(date)
        return None

    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()
        _force_ns_for_doc(root)

        header_dtms = list(root.findall("./{*}S_DTM"))
        seen = {id(node) for node in header_dtms}
        all_dtms = header_dtms.copy()
        for node in root.findall(".//{*}S_DTM"):
            if id(node) not in seen:
                all_dtms.append(node)
                seen.add(id(node))

        for nodes in (header_dtms, all_dtms[len(header_dtms) :]):
            if not nodes:
                continue
            for qualifier in ("35", "137"):
                date = _find_date(nodes, qualifier)
                if date:
                    return date
    except Exception:
        pass
    return None


# ───────────────────── številka računa ─────────────────────
def extract_invoice_number(xml_path: Path | str) -> str | None:
    """Vrne številko računa iz dokumenta."""
    try:
        tree = LET.parse(xml_path, parser=XML_PARSER)
        root = tree.getroot()

        # --- UBL ---
        try:
            id_el = root.find(".//cbc:ID", UBL_NS)
        except Exception:
            id_el = None
        if id_el is not None:
            num = _text(id_el)
            if num:
                log.debug("Extracted invoice ID from UBL: %s", num)
                return num

        # --- EDIFACT BGM fallback ---
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
                    log.debug("Extracted invoice ID from BGM: %s", num)
                    return num
    except Exception:
        pass
    return None


def extract_total_tax(source: Path | str | Any) -> Decimal:
    """Return invoice VAT total using SG52 summary, TaxAmount or MOA 124 fallbacks."""
    try:
        if hasattr(source, "findall"):
            root = source
        else:
            tree = LET.parse(source, parser=XML_PARSER)
            root = tree.getroot()
        _force_ns_for_doc(root)
        summary = _parse_tax_summary(root)
        if summary.has_complete:
            return summary.tax_total
        if summary.partial_tax_total != 0:
            return summary.partial_tax_total

        total = Decimal("0")
        header_tax = Decimal("0")
        line_tax = Decimal("0")

        def _is_in_sg26(node: LET._Element) -> bool:
            anc = node.getparent()
            while anc is not None:
                if anc.tag.split("}")[-1] == "G_SG26":
                    return True
                anc = anc.getparent()
            return False

        # Fallback to explicit TaxAmount in SG34 (prefer header-level)
        for tax_el in root.findall(".//e:G_SG34//e:TaxAmount", NS) + root.findall(
            ".//G_SG34//TaxAmount"
        ):
            val = _decimal(tax_el)
            if _is_in_sg26(tax_el):
                line_tax += val
            else:
                header_tax += val
        total = header_tax if header_tax != 0 else line_tax

        if total == 0:
            for moa in root.findall(".//e:G_SG34/e:S_MOA", NS) + root.findall(
                ".//G_SG34/S_MOA"
            ):
                code_el = moa.find("./e:C_C516/e:D_5025", NS)
                if code_el is None:
                    code_el = moa.find("./C_C516/D_5025")
                if _text(code_el) == "124":
                    val_el = moa.find("./e:C_C516/e:D_5004", NS)
                    if val_el is None:
                        val_el = moa.find("./C_C516/D_5004")
                    val = _decimal(val_el)
                    if _is_in_sg26(moa):
                        line_tax += val
                    else:
                        header_tax += val
            total = header_tax if header_tax != 0 else line_tax

        return _dec2(total)
    except Exception:
        return Decimal("0")


def _parse_tax_summary(root: LET._Element) -> SimpleNamespace:
    """Return tax summary totals from ``G_SG52`` with swapped MOA handling."""
    try:
        _force_ns_for_doc(root)
        base_total = Decimal("0")
        tax_total = Decimal("0")
        partial_tax = Decimal("0")
        base_only_total = Decimal("0")
        has_complete = False
        has_partial_tax = False
        for sg52 in root.findall(".//e:G_SG52", NS) + root.findall(".//G_SG52"):
            amounts: dict[str, Decimal] = {}
            for moa in sg52.findall("./e:S_MOA", NS) + sg52.findall("./S_MOA"):
                code_el = moa.find("./e:C_C516/e:D_5025", NS)
                if code_el is None:
                    code_el = moa.find("./C_C516/D_5025")
                qualifier = _text(code_el)
                if qualifier not in {"124", "125"}:
                    continue
                val_el = moa.find("./e:C_C516/e:D_5004", NS)
                if val_el is None:
                    val_el = moa.find("./C_C516/D_5004")
                amounts[qualifier] = amounts.get(qualifier, Decimal("0")) + _decimal(
                    val_el
                )

            def _rate_for_summary(node: LET._Element) -> Decimal:
                rate = Decimal("0")
                for tax in node.findall("./e:S_TAX", NS) + node.findall("./S_TAX"):
                    r_el = tax.find("./e:C_C243/e:D_5278", NS)
                    if r_el is None:
                        r_el = tax.find("./C_C243/D_5278")
                    r = _decimal(r_el)
                    if r != 0:
                        rate = r
                        break
                return rate

            base_val = amounts.get("125")
            tax_val = amounts.get("124")
            rate_percent = _rate_for_summary(sg52)

            if base_val is not None and tax_val is not None:
                base = base_val
                tax = tax_val
                swapped = False
                if abs(tax) > abs(base):
                    swapped = True
                if rate_percent:
                    expected_tax = _dec2(abs(base) * rate_percent / Decimal("100"))
                    alt_expected = _dec2(abs(tax) * rate_percent / Decimal("100"))
                    if abs(abs(tax) - expected_tax) > Decimal("0.02") and abs(
                        abs(base) - alt_expected
                    ) <= Decimal("0.02"):
                        swapped = True
                if swapped:
                    base, tax = tax_val, base_val
                base_total += base
                tax_total += tax
                has_complete = True
            elif tax_val is not None:
                partial_tax += tax_val
                has_partial_tax = True
            elif base_val is not None:
                base_only_total += base_val
        return SimpleNamespace(
            base_total=_dec2(base_total) if base_total != 0 else Decimal("0"),
            tax_total=_dec2(tax_total) if tax_total != 0 else Decimal("0"),
            partial_tax_total=_dec2(partial_tax)
            if partial_tax != 0
            else Decimal("0"),
            base_only_total=_dec2(base_only_total)
            if base_only_total != 0
            else Decimal("0"),
            has_complete=has_complete,
            has_partial=has_partial_tax,
        )
    except Exception:
        return SimpleNamespace(
            base_total=Decimal("0"),
            tax_total=Decimal("0"),
            partial_tax_total=Decimal("0"),
            base_only_total=Decimal("0"),
            has_complete=False,
            has_partial=False,
        )


def extract_header_totals_preferred(
    source: Path | str | Any,
    *,
    net_fallback: Decimal | None = None,
    tax_fallback: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal, dict[str, Any]]:
    """Return (net, vat, gross) totals preferring MOA 9/79 with robust TAX.

    The totals prioritise MOA 9 for the gross (``payable``) amount and MOA 79
    for the net base.  VAT is primarily derived from ``MOA9 - MOA79``.  When
    tax summary values (``G_SG52``) contain swapped MOA 124/125 values, the
    function treats the larger value as the base and the smaller as VAT.  Any
    missing header amount falls back to the provided ``net_fallback`` or
    ``tax_fallback``.
    """

    try:
        if hasattr(source, "findall"):
            root = source
        else:
            tree = LET.parse(source, parser=XML_PARSER)
            root = tree.getroot()
        _force_ns_for_doc(root)

        gross_candidates: list[tuple[Decimal, str]] = []
        gross9 = _first_moa(root, {"9"}, ignore_sg26=True)
        if gross9 != 0:
            gross_candidates.append((gross9, "MOA9"))
        gross388 = _first_moa(root, {"388"}, ignore_sg26=True)
        if gross388 != 0 and gross388 not in {g for g, _ in gross_candidates}:
            gross_candidates.append((gross388, "MOA388"))
        gross77 = _first_moa(root, {"77"}, ignore_sg26=True)
        if gross77 != 0:
            gross_candidates.append((gross77, "MOA77"))
        gross_total: Decimal | None = None
        gross_source = ""
        if gross_candidates:
            gross_total, gross_source = gross_candidates[0]

        net_raw = _first_moa(root, {"79"}, ignore_sg26=True)
        net_source = "MOA79" if net_raw != 0 else ""
        net_total: Decimal | None = _dec2(net_raw) if net_raw != 0 else None
        if net_total is None:
            net_alt = _first_moa(
                root, {Moa.HEADER_NET.value, "389"}, ignore_sg26=True
            )
            if net_alt != 0:
                net_total = _dec2(net_alt)
                net_source = "MOA389"
        if net_total is None and net_fallback is not None:
            net_total = _dec2(net_fallback)
            if not net_source:
                net_source = "fallback-net"
        net_hint_q: Decimal | None = None
        net_hint = extract_header_net(root)
        if net_hint != 0:
            net_hint_q = _dec2(net_hint)

        summary = _parse_tax_summary(root)

        tax_hint: Decimal | None = None
        if summary.has_complete and summary.tax_total != 0:
            tax_hint = summary.tax_total
        elif summary.has_partial and summary.partial_tax_total != 0:
            tax_hint = summary.partial_tax_total

        def _candidate(
            gross: Decimal | None, net: Decimal | None, label: str
        ) -> tuple[Decimal, Decimal, Decimal, str] | None:
            if gross is None or net is None:
                return None
            vat = _dec2(gross - net)
            return _dec2(net), vat, _dec2(gross), label

        candidates: list[tuple[Decimal, Decimal, Decimal, str]] = []

        for g_val, g_src in gross_candidates or [(gross_total, gross_source)]:
            g_val_q = _dec2(g_val) if g_val is not None else None
            cand_standard = _candidate(
                g_val_q,
                net_total,
                f"{g_src}-MOA79" if g_src else "MOA9-79",
            )
            if cand_standard:
                candidates.append(cand_standard)
            if net_hint_q is not None:
                cand_hdr = _candidate(g_val_q, net_hint_q, f"{g_src}-header_net")
                if cand_hdr:
                    candidates.append(cand_hdr)
            cand_swap = _candidate(
                _dec2(net_total) if net_total is not None else None,
                g_val_q,
                f"{g_src}-swap" if g_src else "MOA79-9",
            )
            if cand_swap:
                candidates.append(cand_swap)
            if tax_hint is not None:
                cand_tax_hint = _candidate(
                    g_val_q,
                    _dec2(g_val_q - tax_hint) if g_val_q is not None else None,
                    f"{g_src}-taxhint" if g_src else "MOA9-taxhint",
                )
                if cand_tax_hint:
                    candidates.append(cand_tax_hint)

        def _score(
            cand: tuple[Decimal, Decimal, Decimal, str]
        ) -> tuple[Decimal, bool, Decimal, Decimal]:
            net_c, vat_c, gross_c, label = cand
            gross_net_ok = abs(gross_c) + Decimal("0.05") >= abs(net_c)
            priority = Decimal("5")
            if "header_net" in label:
                priority = Decimal("0")
            elif "MOA9" in label and "MOA79" in label and "swap" not in label:
                priority = Decimal("1")
            elif "swap" in label or label == "MOA79-9":
                priority = Decimal("2")
            elif "taxhint" in label:
                priority = Decimal("3")

            if tax_hint is None:
                return (Decimal("0"), gross_net_ok, priority, abs(vat_c))
            sign_ok = vat_c == 0 or vat_c * tax_hint >= 0
            diff = abs(vat_c - tax_hint)
            return (diff, sign_ok and gross_net_ok, priority, abs(vat_c))

        best: tuple[Decimal, Decimal, Decimal, str] | None = None
        if candidates:
            scored = []
            for cand in candidates:
                diff, valid, priority, vat_abs = _score(cand)
                scored.append((diff, valid, priority, vat_abs, cand))
            # Prefer valid sign/ratio, then smallest diff, then priority, then |vat|
            scored.sort(key=lambda item: (not item[1], item[0], item[2], item[3]))
            best = scored[0][4]

        vat_source = ""
        if best is None:
            # derive from available hints or fallbacks
            if gross_total is None and net_total is not None and tax_hint is not None:
                gross_total = _dec2(net_total + tax_hint)
                gross_source = gross_source or "net+taxhint"
            if net_total is None and gross_total is not None and tax_hint is not None:
                net_total = _dec2(gross_total - tax_hint)
                net_source = net_source or "gross-taxhint"
            if gross_total is None and net_total is None and net_fallback is not None:
                net_total = _dec2(net_fallback)
                net_source = net_source or "fallback-net"
            if gross_total is None and tax_fallback is not None and net_total is not None:
                gross_total = _dec2(net_total + _dec2(tax_fallback))
                gross_source = gross_source or "net+fallback-tax"
            vat_total = (
                _dec2(tax_fallback) if tax_fallback is not None else Decimal("0")
            )
            vat_source = "fallback-tax" if tax_fallback is not None else "calculated"
            if gross_total is None:
                gross_total = _dec2((net_total or Decimal("0")) + vat_total)
                if not gross_source:
                    gross_source = "net+vat"
            if net_total is None:
                net_total = Decimal("0")
                if not net_source:
                    net_source = "calculated"
        else:
            net_total, vat_total, gross_total, variant = best
            vat_source = variant
            is_swap = ("swap" in variant) or (variant == "MOA79-9")
            if is_swap:
                gross_source, net_source = "MOA79", "MOA9"
            elif "taxhint" in variant:
                net_source = "gross-taxhint"
            else:
                gross_source = gross_source or "MOA9/388"
                net_source = net_source or "MOA79"
            # derive precise gross source from variant prefix only when not swapped
            if not is_swap:
                prefix = variant.split("-")[0]
                if prefix in {"MOA9", "MOA388", "MOA77"}:
                    gross_source = prefix
            if tax_hint is not None and abs(vat_total - tax_hint) <= Decimal("0.02"):
                vat_source = f"{variant}+SG52"

        meta = {
            "gross_source": gross_source or "calculated",
            "net_source": net_source or "calculated",
            "vat_source": vat_source or "calculated",
            "tax_summary_complete": summary.has_complete,
            "tax_summary_partial": summary.has_partial,
        }
        return net_total, vat_total, gross_total, meta
    except Exception:
        return (
            _dec2(net_fallback) if net_fallback is not None else Decimal("0"),
            _dec2(tax_fallback) if tax_fallback is not None else Decimal("0"),
            _dec2(
                (net_fallback or Decimal("0")) + (tax_fallback or Decimal("0"))
            ),
            {
                "gross_source": "error",
                "net_source": "error",
                "vat_source": "error",
                "tax_summary_complete": False,
                "tax_summary_partial": False,
            },
        )


def sum_moa(
    root: LET._Element,
    codes: List[str],
    *,
    tax_amount: Decimal | None = None,
    doc_level_only: bool = False,
) -> Decimal:
    """Return the sum of MOA amounts for the given codes.

    Only ``S_MOA`` elements that appear within allowance/charge segments
    (``S_ALC``) are considered.  Segments nested inside tax summary
    groups (``G_SG52``) are ignored.  Amounts matching ``tax_amount`` are
    skipped to avoid mistaking VAT totals for discounts.
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
            if doc_level_only and ancestor.tag.split("}")[-1] == "G_SG26":
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

            if tax_amount is not None and val == tax_amount:
                continue
            total += val

    # Scan header MOA segments (G_SG50) without S_ALC
    for sg50 in root.findall(".//e:G_SG50", NS) + root.findall(".//G_SG50"):
        ancestor = sg50.getparent()
        skip = False
        while ancestor is not None:
            if ancestor.tag.split("}")[-1] == "G_SG52":
                skip = True
                break
            ancestor = ancestor.getparent()
        if skip:
            continue
        if (
            sg50.find("./e:S_ALC", NS) is not None
            or sg50.find("./S_ALC") is not None
        ):
            continue
        for moa in sg50.findall("./e:S_MOA", NS) + sg50.findall("./S_MOA"):
            code_el = moa.find("./e:C_C516/e:D_5025", NS)
            if code_el is None:
                code_el = moa.find("./C_C516/D_5025")
            if code_el is None or _text(code_el) not in wanted:
                continue
            val_el = moa.find("./e:C_C516/e:D_5004", NS)
            if val_el is None:
                val_el = moa.find("./C_C516/D_5004")
            val = _decimal(val_el)

            if tax_amount is not None and val == tax_amount:
                continue
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
    """Return discount amount for a line (sum of direct MOA 204 values)."""
    if _INFO_DISCOUNTS:
        return Decimal("0")
    total = Decimal("0")
    if hasattr(sg26, "xpath"):
        nodes = sg26.xpath(
            "./e:S_MOA[e:C_C516/e:D_5025='204']/e:C_C516/e:D_5004",
            namespaces=NS,
        ) + sg26.xpath("./S_MOA[C_C516/D_5025='204']/C_C516/D_5004")
    else:
        nodes = []
        for moa in sg26.findall("./e:S_MOA", NS) + sg26.findall("./S_MOA"):
            code_el = moa.find("./e:C_C516/e:D_5025", NS) or moa.find(
                "./C_C516/D_5025"
            )
            if _text(code_el) != "204":
                continue
            val_el = moa.find("./e:C_C516/e:D_5004", NS) or moa.find(
                "./C_C516/D_5004"
            )
            if val_el is not None:
                nodes.append(val_el)
    for amt_el in nodes:
        total += _decimal(amt_el).quantize(DEC2, ROUND_HALF_UP)

    if hasattr(sg26, "xpath"):
        pct_nodes = sg26.xpath(
            "./e:S_PCD[e:C_C501/e:D_5245='1']/e:C_C501/e:D_5482", namespaces=NS
        )
        if not pct_nodes:
            pct_nodes = sg26.xpath("./S_PCD[C_C501/D_5245='1']/C_C501/D_5482")
    else:
        pct_nodes = []
        for pcd in sg26.findall("./e:S_PCD", NS) + sg26.findall("./S_PCD"):
            qual_el = pcd.find("./e:C_C501/e:D_5245", NS) or pcd.find(
                "./C_C501/D_5245"
            )
            if _text(qual_el) != "1":
                continue
            val_el = pcd.find("./e:C_C501/e:D_5482", NS) or pcd.find(
                "./C_C501/D_5482"
            )
            if val_el is not None:
                pct_nodes.append(val_el)
    pct = _decimal(pct_nodes[0] if pct_nodes else None)
    if pct != 0:
        base_nodes = sg26.xpath(
            "./e:S_PRI[e:C_C509/e:D_5125='AAB']/e:C_C509/e:D_5118",
            namespaces=NS,
        )
        if not base_nodes:
            base_nodes = sg26.xpath(
                "./S_PRI[C_C509/D_5125='AAB']/C_C509/D_5118"
            )
        qty_el = sg26.find("./e:S_QTY/e:C_C186/e:D_6060", NS) or sg26.find(
            "./S_QTY/C_C186/D_6060"
        )
        base = _decimal(base_nodes[0] if base_nodes else None) * _decimal(
            qty_el
        )
        if base == 0:
            base_nodes = sg26.xpath(
                "./e:S_MOA[e:C_C516/e:D_5025='38']/e:C_C516/e:D_5004",
                namespaces=NS,
            )
            if not base_nodes:
                base_nodes = sg26.xpath(
                    "./S_MOA[C_C516/D_5025='38']/C_C516/D_5004"
                )
            base = _decimal(base_nodes[0] if base_nodes else None)
        total += (base * pct / Decimal("100")).quantize(DEC2, ROUND_HALF_UP)

    return total.quantize(DEC2, ROUND_HALF_UP)


def _line_amount_discount(sg26: LET._Element) -> Decimal:
    """Return sum of MOA 204 allowance amounts for a line."""
    if _INFO_DISCOUNTS:
        return Decimal("0")
    total = Decimal("0")
    paths = (
        "./e:G_SG39/e:S_MOA[e:C_C516/e:D_5025='204']/e:C_C516/e:D_5004",  # noqa: E501
        "./G_SG39/S_MOA[C_C516/D_5025='204']/C_C516/D_5004",  # noqa: E501
    )
    for path in paths:
        for amt_el in sg26.xpath(path, namespaces=NS):
            total += _decimal(amt_el).quantize(DEC2, ROUND_HALF_UP)

    return total.quantize(DEC2, ROUND_HALF_UP)


def _pct_base(sg39: LET._Element, sg26: LET._Element) -> Decimal:
    """Return base amount for percentage discounts."""

    base = _first_moa(sg39, BASE_MOA_LINE)
    if base != 0:
        return base

    base = _line_moa203(sg26)
    if base != 0:
        return base

    qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
    if qty == 0:
        return Decimal("0")

    price = Decimal("0")
    for pri in sg26.findall(".//e:S_PRI", NS) + sg26.findall(".//S_PRI"):
        code_el = pri.find("./e:C_C509/e:D_5125", NS)
        if code_el is None:
            code_el = pri.find("./C_C509/D_5125")
        if _text(code_el) == "AAA":
            val_el = pri.find("./e:C_C509/e:D_5118", NS)
            if val_el is None:
                val_el = pri.find("./C_C509/D_5118")
            price = _decimal(val_el)
            break

    return price * qty


def _line_pct_discount(sg26: LET._Element) -> Decimal:
    """Return discount amount calculated from ``G_SG39`` percentage values."""
    if _INFO_DISCOUNTS:
        return Decimal("0")
    total = Decimal("0")

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
        if pct == 0:
            continue
        base = _pct_base(sg39, sg26)
        if base == 0:
            continue
        if qualifier == "1":
            total += base * pct / Decimal("100")
        elif qualifier == "2":
            total += base * (Decimal("1") - pct)
        else:  # qualifier == "3"
            total += pct

    return total.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _line_amount_after_allowances(seg: LET._Element) -> Decimal:
    """Return line amount after sequential SG39 allowances/charges."""
    base = sum(
        (_sum_moa(sg27, {"203"}, deep=False))
        for sg27 in seg.findall("./e:G_SG27", NS) + seg.findall("./G_SG27")
    )
    if isinstance(base, int):
        base = Decimal(base)
    if base == 0:
        base = _line_moa203(seg)
    run = base
    for sg39, kind, pcds, moa_allow, moa_charge in _iter_sg39(seg):
        pct_base = _pct_base(sg39, seg)
        for pct in pcds:
            amt = _dec2(pct_base * pct / Decimal("100"))
            if kind == "A":
                amt = -amt
            run += amt
        run -= moa_allow
        run += moa_charge
        if base >= 0 and run < 0:
            run = Decimal("0")
        elif base < 0 and run > 0:
            run = Decimal("0")
    return _dec2(run)


def _line_discount_components(
    sg26: LET._Element,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return line discount components with MOA 204 preferred over
    matching PCD."""
    disc_direct = _line_discount(sg26)
    disc_moa = _line_amount_discount(sg26)
    pct_disc = _line_pct_discount(sg26)
    if (
        disc_direct != 0
        and pct_disc != 0
        and abs(disc_direct - pct_disc) <= TOL
    ):
        pct_disc = Decimal("0")
    return disc_direct, disc_moa, pct_disc


def _doc_discount_from_line(seg: LET._Element) -> Decimal | None:
    base = sum(
        _sum_moa(sg27, {"203"}, deep=False)
        for sg27 in seg.findall("./e:G_SG27", NS) + seg.findall("./G_SG27")
    )
    if base == 0:
        base = _first_moa(seg, {"125"})
    disc_local = -_sum_moa(
        seg, DISCOUNT_MOA_LINE | DOC_DISCOUNT_MOA, deep=False
    )
    sg39_total = Decimal("0")
    for sg39 in seg.findall("./e:G_SG39", NS) + seg.findall("./G_SG39"):
        alc = sg39.find("./e:S_ALC/e:D_5463", NS)
        if alc is None:
            alc = sg39.find("./S_ALC/D_5463")
        if (alc.text or "").strip() != "A":
            continue
        pcds = _get_pcd_shallow(sg39)
        pct_base = _pct_base(sg39, seg)
        for pct in pcds:
            amt = _dec2(pct_base * pct / Decimal("100"))
            disc_local -= amt
            sg39_total -= amt
        moa_allow = _sum_moa(
            sg39, DISCOUNT_MOA_LINE | DOC_DISCOUNT_MOA, deep=False
        )
        disc_local -= moa_allow
        sg39_total -= moa_allow
    if base == 0 and (disc_local != 0 or sg39_total != 0):
        return _dec2(disc_local)
    return None


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
    """Return net line amount excluding VAT with line discounts applied."""

    base = _line_moa203(sg26)
    has_moa204 = _sum_moa(sg26, DISCOUNT_MOA_LINE, deep=True) != 0
    val = _first_moa(sg26, {"125"})
    if base == 0:
        if val != 0:
            net = _dec2(val)
            if not has_moa204:
                net -= _line_pct_discount(sg26)
            return _dec2(net)

    if val != 0:
        net = _dec2(val)
        if not has_moa204:
            net -= _line_pct_discount(sg26)
        return _dec2(net)

    fallback_price = _line_gross(sg26)
    if fallback_price != 0:
        return _dec2(fallback_price)

    return _line_amount_after_allowances(sg26)


def _line_net_before_discount(
    sg26: LET._Element, net_after: Decimal | None = None
) -> Decimal:
    """Return net line amount excluding VAT before applying discounts."""

    if net_after is None:
        net_after = _line_net(sg26)
    disc_direct, disc_moa, pct_disc = _line_discount_components(sg26)
    discount = disc_direct + disc_moa + pct_disc
    return (net_after + discount).quantize(DEC2, ROUND_HALF_UP)


def _line_net_standard(
    sg26: LET._Element, base203: Decimal | None = None
) -> Decimal:
    """Return net amount minus only MOA 204 and PCD-based discounts."""

    if base203 is None:
        base203 = _line_moa203(sg26)
        if base203 == 0:
            val = _first_moa(sg26, {"125"})
            base203 = _dec2(val) if val != 0 else Decimal("0.00")

    net = base203
    net -= _sum_moa(sg26, DISCOUNT_MOA_LINE, deep=False)
    for sg39 in sg26.findall("./e:G_SG39", NS) + sg26.findall("./G_SG39"):
        net -= _sum_moa(sg39, DISCOUNT_MOA_LINE, deep=False)
    net -= _line_pct_discount(sg26)
    return _dec2(net)


def _line_tax(
    sg26: LET._Element, default_rate: Decimal | None = None
) -> tuple[Decimal, Decimal]:
    """Return VAT amount and rate for a line.

    ``default_rate`` should be provided as a fraction (e.g. ``0.22`` for
    22 %).  The function prefers explicit ``TaxAmount`` elements inside
    ``G_SG34`` or ``G_SG52`` groups.  When a rate is missing or does not
    match the provided tax amount, it is inferred from ``tax_amount /
    net_amount``.
    """

    net_amount = _line_net(sg26)

    # --- explicit TaxAmount (cbc or e namespace) ---
    tax_el = None
    paths_tax = (
        ".//e:G_SG34//cbc:TaxAmount",
        ".//e:G_SG34//e:TaxAmount",
        ".//e:G_SG34//TaxAmount",
        ".//e:G_SG52//cbc:TaxAmount",
        ".//e:G_SG52//e:TaxAmount",
        ".//e:G_SG52//TaxAmount",
    )
    for path in paths_tax:
        tax_el = sg26.find(path, {**NS, **UBL_NS})
        if tax_el is not None and _text(tax_el):
            break

    if tax_el is not None and _text(tax_el):
        tax_amount = _decimal(tax_el).quantize(DEC2, ROUND_HALF_UP)
        rate_percent = Decimal("0")
        for path in (".//e:G_SG34/e:S_TAX", ".//e:G_SG52/e:S_TAX"):
            for tax in sg26.findall(path, NS):
                r = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
                if r:
                    rate_percent = r
                    break
            if rate_percent:
                break
        if rate_percent == 0 and default_rate is not None:
            rate_percent = (default_rate * 100).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
        expected_tax = (
            calculate_vat(net_amount, rate_percent)
            if rate_percent
            else tax_amount
        )
        if net_amount and (rate_percent == 0 or expected_tax != tax_amount):
            rate_percent = (tax_amount / net_amount * 100).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
        return tax_amount, rate_percent

    # --- MOA 124 ---
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
        tax_amount = abs_tax.quantize(DEC2, ROUND_HALF_UP)
        rate_percent = Decimal("0")
        for path in (".//e:G_SG34/e:S_TAX", ".//e:G_SG52/e:S_TAX"):
            for tax in sg26.findall(path, NS):
                r = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
                if r:
                    rate_percent = r
                    break
            if rate_percent:
                break
        if rate_percent == 0 and default_rate is not None:
            rate_percent = (default_rate * 100).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
        return tax_amount, rate_percent

    # --- fallback to rate from S_TAX or default ---
    rate_percent = Decimal("0")
    for path in (".//e:G_SG34/e:S_TAX", ".//e:G_SG52/e:S_TAX"):
        for tax in sg26.findall(path, NS):
            r = _decimal(tax.find("./e:C_C243/e:D_5278", NS))
            if r:
                rate_percent = r
                break
        if rate_percent:
            break
    if rate_percent == 0 and default_rate is not None:
        rate_percent = (default_rate * 100).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )

    tax_amount = (
        calculate_vat(net_amount, rate_percent)
        if rate_percent
        else Decimal("0.00")
    )
    return tax_amount, rate_percent


def _alc_pcd_moa_discount(sg26: LET._Element, qty: Decimal) -> tuple[Decimal, Decimal, bool]:
    """Extract discount percent/amount from ``G_SG39`` ALC/PCD/MOA segments."""
    pri_nodes = sg26.xpath(
        ".//e:S_PRI[e:C_C509/e:D_5125='AAA']/e:C_C509/e:D_5118",
        namespaces=NS,
    )
    if pri_nodes:
        unit_price_after = _decimal(pri_nodes[0])
    else:
        nodes = sg26.xpath(
            ".//S_PRI[C_C509/D_5125='AAA']/C_C509/D_5118"
        )
        unit_price_after = _decimal(nodes[0]) if nodes else None

    pri_nodes = sg26.xpath(
        ".//e:S_PRI[e:C_C509/e:D_5125='AAB']/e:C_C509/e:D_5118",
        namespaces=NS,
    )
    if pri_nodes:
        unit_price_list = _decimal(pri_nodes[0])
    else:
        nodes = sg26.xpath(
            ".//S_PRI[C_C509/D_5125='AAB']/C_C509/D_5118"
        )
        unit_price_list = _decimal(nodes[0]) if nodes else None

    moa_nodes = sg26.xpath(
        ".//e:S_MOA[e:C_C516/e:D_5025='203']/e:C_C516/e:D_5004",
        namespaces=NS,
    )
    if not moa_nodes:
        moa_nodes = sg26.xpath(
            ".//S_MOA[C_C516/D_5025='203']/C_C516/D_5004"
        )
    moa203 = _decimal(moa_nodes[0]) if moa_nodes else None

    discount_pct = Decimal("0")
    discount_amt = Decimal("0")
    has_charge = False
    for sg39 in sg26.findall(".//e:G_SG39", NS) + sg26.findall(".//G_SG39"):
        alc_code = (
            _text(sg39.find("./e:S_ALC/e:D_5463", NS))
            or _text(sg39.find("./S_ALC/D_5463"))
            or ""
        ).strip()
        if alc_code == "C":
            has_charge = True
        if alc_code != "A":
            continue
        for pcd in sg39.findall(".//e:S_PCD", NS) + sg39.findall(".//S_PCD"):
            qual = _text(pcd.find("./e:C_C501/e:D_5245", NS)) or _text(
                pcd.find("./C_C501/D_5245")
            )
            if qual.strip() == "1":
                val_el = pcd.find("./e:C_C501/e:D_5482", NS) or pcd.find(
                    "./C_C501/D_5482"
                )
                val = _decimal(val_el)
                if val:
                    discount_pct = val
        for moa in sg39.findall(".//e:S_MOA", NS) + sg39.findall(".//S_MOA"):
            qual = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
                moa.find("./C_C516/D_5025")
            )
            if qual.strip() == "204":
                val_el = moa.find("./e:C_C516/e:D_5004", NS) or moa.find(
                    "./C_C516/D_5004"
                )
                discount_amt += _decimal(val_el)

    if (
        discount_pct == 0
        and unit_price_list is not None
        and unit_price_after is not None
        and unit_price_list
        and not has_charge
    ):
        try:
            discount_pct = (
                (unit_price_list - unit_price_after)
                / unit_price_list
                * Decimal("100")
            )
        except Exception:
            pass
    if (
        discount_amt == 0
        and unit_price_list is not None
        and unit_price_after is not None
    ):
        discount_amt = (unit_price_list - unit_price_after) * qty
    if discount_amt == 0 and unit_price_list is not None and moa203 is not None:
        discount_amt = unit_price_list * qty - moa203
    if (
        discount_pct == 0
        and unit_price_list is not None
        and moa203 is not None
        and unit_price_list * qty
    ):
        try:
            discount_pct = (
                (unit_price_list * qty - moa203)
                / (unit_price_list * qty)
                * Decimal("100")
            )
        except Exception:
            pass

    is_gratis = bool(
        discount_pct >= 100
        or (
            unit_price_after is not None
            and unit_price_after == 0
            and unit_price_list is not None
            and unit_price_list > 0
        )
    )
    if discount_pct < 0 or discount_amt < 0:
        is_gratis = False
    if is_gratis and discount_pct < 100:
        discount_pct = Decimal("100")

    # vrni lepo zaokroženo na 2 decimalki
    q2 = Decimal("0.01")
    return (
        discount_pct.quantize(q2, ROUND_HALF_UP),
        discount_amt.quantize(q2, ROUND_HALF_UP),
        is_gratis,
    )


# ──────────────────── glavni parser za ESLOG INVOIC ────────────────────
def parse_eslog_invoice(
    xml_path: str | Path,
    discount_codes: List[str] | None = None,
    _mode_override: str | None = None,
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
    _mode_override : str | None, optional
        Internal override for calculation mode ("info" or "real").
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
    _force_ns_for_doc(root)
    supplier_code = get_supplier_info(tree)
    header_rate = _tax_rate_from_header(root)
    items: List[Dict] = []
    net_total = Decimal("0")
    tax_total = Decimal("0")
    lines_by_rate: Dict[Decimal, Decimal] = {}
    vat_mismatch = False
    doc_discount_from_lines = Decimal("0")
    line_logs: list[dict[str, Any]] = []
    line_items: list[tuple[LET._Element, Decimal, Decimal]] = []
    lines_by_rate_info: Dict[Decimal, Decimal] = {}
    lines_by_rate_std: Dict[Decimal, Decimal] = {}

    # ───────────── LINE ITEMS ─────────────
    for idx, sg26 in enumerate(root.findall(".//e:G_SG26", NS)):
        base203 = _line_moa203(sg26)
        doc_disc_raw = _doc_discount_from_line(sg26)
        add_doc = Decimal("0.00")
        if doc_disc_raw is not None and base203 == 0:
            add_doc = doc_disc_raw
            doc_discount_from_lines += add_doc
        qty = _decimal(sg26.find(".//e:S_QTY/e:C_C186/e:D_6060", NS))
        unit = _text(sg26.find(".//e:S_QTY/e:C_C186/e:D_6411", NS))
        net_std = _line_net_standard(sg26, base203)
        item: Dict[str, Any] = {
            "_idx": idx,
            "_base203": base203,
            "_net_std": net_std,
        }
        line_items.append((sg26, base203, net_std))

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
        net_amount_code = ""
        net_203 = base203 if base203 != 0 else None
        net_125_val = _first_moa(sg26, {"125"})
        net_125 = _dec2(net_125_val) if net_125_val != 0 else None
        for candidate in ("125", Moa.NET.value):
            val = _first_moa(sg26, {candidate})
            if val != 0:
                net_amount_moa = _dec2(val)
                net_amount_code = candidate
                break

        net_amount = _line_net(sg26)
        net_before = _line_net_before_discount(sg26, net_amount)
        disc_direct, disc_moa, pct_disc = _line_discount_components(sg26)
        rebate = disc_direct + disc_moa + pct_disc
        explicit_pct: Decimal | None = None
        pct_fallback, amt_fallback, gratis_fallback = _alc_pcd_moa_discount(sg26, qty)
        # če smo dobili znesek popusta, ga uporabi
        if rebate == 0 and amt_fallback != 0:
            rebate = amt_fallback
        # % je samo za prikaz – ne mešamo ga z zneskom
        if explicit_pct is None and pct_fallback != 0:
            explicit_pct = pct_fallback.quantize(Decimal("0.01"), ROUND_HALF_UP)
        # če zneska ni, ga lahko izračunamo iz % in net_before
        if rebate == 0 and pct_fallback != 0 and net_before > 0:
            rebate = (pct_fallback / Decimal("100")) * net_before

        # Če smo popust inferirali in MOA 203 ni podal bruto zneska, dvigni net_before
        if rebate > 0 and net_before == net_amount:
            net_before = (net_amount + rebate).quantize(Decimal("0.01"), ROUND_HALF_UP)

        tax_amount, vat_rate = _line_tax(
            sg26, header_rate if header_rate != 0 else None
        )
        if tax_amount is None:
            vat_mismatch = True
            tax_amount = Decimal("0")
        if net_amount == 0 and gross_amount != 0:
            net_amount = (gross_amount - rebate - tax_amount).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            net_before = (net_amount + rebate).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )

        if net_amount == 0 and net_before > 0:
            doc_discount_from_lines += net_before
            add_doc += net_before
            tax_amount = Decimal("0")

        item["_pre_doc_net"] = net_amount
        item["ddv"] = tax_amount
        if net_203 is not None:
            item["net_203"] = net_203
        if net_125 is not None:
            item["net_125"] = net_125

        if (
            net_amount_moa is not None
            and abs(net_amount - net_amount_moa) > DEC2
        ):
            log.warning(
                "Line net mismatch: MOA %s %s vs calculated %s",
                net_amount_code or Moa.NET.value,
                net_amount_moa,
                net_amount,
            )

        net_total = (net_total + net_amount).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        tax_total = (tax_total + tax_amount).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        if vat_rate:
            lines_by_rate[vat_rate] = (
                lines_by_rate.get(vat_rate, Decimal("0")) + net_amount
            )
            lines_by_rate_info[vat_rate] = (
                lines_by_rate_info.get(vat_rate, Decimal("0")) + base203
            )
            lines_by_rate_std[vat_rate] = (
                lines_by_rate_std.get(vat_rate, Decimal("0")) + net_std
            )

        line_logs.append(
            {
                "idx": idx,
                "moa203": base203,
                "net_std": net_amount,
                "doc_added": add_doc,
                "carried_doc_disc": add_doc,
            }
        )

        # rabat na ravni vrstice
        for sg39 in sg26.findall(".//e:G_SG39", NS):
            if _text(sg39.find("./e:S_ALC/e:D_5463", NS)) != "A":
                continue
            pct = _decimal(sg39.find("./e:S_PCD/e:C_C501/e:D_5482", NS))
            if pct != 0:
                explicit_pct = pct.quantize(Decimal("0.01"), ROUND_HALF_UP)

        rebate = rebate.quantize(Decimal("0.01"), ROUND_HALF_UP)

        # izračun cen pred in po rabatu
        if qty:
            cena_pred = (net_before / qty).quantize(
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

        eff_discount_pct = rabata_pct
        is_gratis = (qty > 0 and net_amount == 0) or rabata_pct >= Decimal(
            "99.9"
        )
        if not is_gratis and gratis_fallback:
            is_gratis = True
        if is_gratis and rabata_pct < Decimal("100"):
            rabata_pct = Decimal("100")
            eff_discount_pct = rabata_pct
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
                "eff_discount_pct": eff_discount_pct,
                "line_bucket": (
                    eff_discount_pct,
                    cena_post.quantize(DEC4, rounding=ROUND_HALF_UP),
                ),
                "is_gratis": is_gratis,
                "vrednost": net_amount,
                "ddv_stopnja": vat_rate,
                "sifra_artikla": art_code,
            }
        )

        if "ddv" not in item:
            item["ddv"] = Decimal("0")

        _t(
            "line desc=%r qty=%s net=%s gross?=%s "
            "rabat=%s pct=%s gratis=%s bucket=%s",
            desc,
            qty,
            net_amount,
            net_before,
            rebate,
            eff_discount_pct,
            is_gratis,
            item.get("line_bucket"),
        )
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

    # ───────── POST LINE CHECK ─────────
    doc_net: Decimal | None = None
    hdr389 = _first_moa(root, {"389"}, ignore_sg26=True)
    if hdr389 != 0:
        doc_net = _dec2(hdr389)
    else:
        hdr79 = _first_moa(root, {"79"}, ignore_sg26=True)
        if hdr79 != 0:
            doc_net = _dec2(hdr79)

    sum_203_vals = [it["net_203"] for it in items if "net_203" in it]
    sum_125_vals = [it["net_125"] for it in items if "net_125" in it]
    sum203 = _dec2(sum(sum_203_vals, Decimal("0"))) if sum_203_vals else None
    sum_125 = _dec2(sum(sum_125_vals, Decimal("0"))) if sum_125_vals else None

    use_203 = False
    use_125 = False
    if sum203 is not None and doc_net is not None and abs(sum203 - doc_net) <= NET_TOL:
        use_203 = True
    elif sum_125 is not None and doc_net is not None and abs(sum_125 - doc_net) <= NET_TOL:
        use_125 = True
    elif sum203 is not None and sum_125 is not None and abs(sum203 - sum_125) <= NET_TOL:
        use_203 = True
    elif sum203 is not None:
        use_203 = True
    elif sum_125 is not None:
        use_125 = True

    for it in items:
        if "_pre_doc_net" not in it:
            continue
        chosen_net: Decimal = Decimal("0")
        if use_203 and "net_203" in it:
            chosen_net = it["net_203"]
        elif use_125 and "net_125" in it:
            chosen_net = it["net_125"]
        elif "net_203" in it:
            chosen_net = it["net_203"]
        elif "net_125" in it:
            chosen_net = it["net_125"]
        else:
            chosen_net = it.get("_pre_doc_net", Decimal("0"))

        it["net"] = chosen_net
        it["_pre_doc_net"] = chosen_net
        it["_net_std"] = chosen_net
        it["vrednost"] = chosen_net
        qty = it.get("kolicina", Decimal("0"))
        if qty:
            it["cena_netto"] = (chosen_net / qty).quantize(
                DEC4, rounding=ROUND_HALF_UP
            )
        idx = it.get("_idx")
        if idx is not None:
            for ln in line_logs:
                if ln.get("idx") == idx:
                    ln["net_std"] = chosen_net
                    break

    net_total = Decimal("0")
    tax_total = Decimal("0")
    lines_by_rate = {}
    for it in items:
        if "_pre_doc_net" not in it:
            continue
        net_total = (net_total + it["_pre_doc_net"]).quantize(DEC2, ROUND_HALF_UP)
        tax_total = (tax_total + it.get("ddv", Decimal("0"))).quantize(
            DEC2, ROUND_HALF_UP
        )
        rate = it.get("ddv_stopnja", Decimal("0"))
        if rate:
            lines_by_rate[rate] = lines_by_rate.get(rate, Decimal("0")) + it[
                "_pre_doc_net"
            ]

    sum203 = _dec2(sum(sum_203_vals, Decimal("0"))) if sum_203_vals else Decimal("0")
    sum_line_net_std = _dec2(
        sum((it["_net_std"] for it in items if "_net_std" in it), Decimal("0"))
    )

    hdr125 = _first_moa(root, {"125"}, ignore_sg26=True)
    hdr125 = hdr125 if hdr125 != 0 else None
    hdr9 = _first_moa(root, {"9", "388"}, ignore_sg26=True)
    hdr9 = hdr9 if hdr9 != 0 else None
    hdr_net = _first_moa(root, {Moa.HEADER_NET.value, "79", "389"}, ignore_sg26=True)
    hdr_net = hdr_net if hdr_net != 0 else None

    sum_lines_net = _dec2(
        sum((it["net"] for it in items if "net" in it and "_pre_doc_net" in it), Decimal("0"))
    )
    net_mismatch = False

    hdr260_present = False
    for moa in root.findall(".//e:S_MOA", NS) + root.findall(".//S_MOA"):
        code = _text(moa.find("./e:C_C516/e:D_5025", NS)) or _text(
            moa.find("./C_C516/D_5025")
        )
        if code != "260":
            continue
        anc = moa.getparent()
        in_sg26 = False
        while anc is not None:
            if anc.tag.split("}")[-1] == "G_SG26":
                in_sg26 = True
                break
            anc = anc.getparent()
        if not in_sg26:
            hdr260_present = True
            break

    def _gross_total(
        base_net: Decimal, doc_disc: Decimal, by_rate: dict[Decimal, Decimal]
    ) -> Decimal:
        net_after_doc, doc_allow_header, _ = _apply_doc_allowances_sequential(
            base_net, root, charge_codes=set(DEFAULT_DOC_CHARGE_CODES)
        )
        net_t = net_after_doc + doc_disc
        doc_allow_total = doc_allow_header + doc_disc
        vat_t = _vat_total_after_doc(None, by_rate, doc_allow_total)
        return (net_t + vat_t).quantize(DEC2, ROUND_HALF_UP)

    gross_info = _gross_total(sum203, Decimal("0"), lines_by_rate_info)
    gross_real = _gross_total(
        sum_line_net_std, doc_discount_from_lines, lines_by_rate_std
    )

    if hdr125 is None:
        info_plausible = False
        real_plausible = True
    else:
        info_plausible = (
            -TOL <= (hdr125 - sum203) <= TOL and not hdr260_present
        )
        real_plausible = (
            -TOL <= (hdr125 - sum_line_net_std) <= TOL or not info_plausible
        )
    if _mode_override is not None:
        mode = _mode_override
    else:
        if info_plausible and real_plausible:
            if hdr9 is None or abs(hdr9 - gross_info) <= abs(
                hdr9 - gross_real
            ):
                mode = "info"
            else:
                mode = "real"
        elif info_plausible:
            mode = "info"
        else:
            mode = "real"

        gross_selected = gross_info if mode == "info" else gross_real
        if hdr9 is not None and abs(gross_selected - hdr9) > TOL:
            other_gross = gross_real if mode == "info" else gross_info
            if abs(hdr9 - other_gross) < abs(gross_selected - hdr9):
                mode = "real" if mode == "info" else "info"

    if mode == "info":
        doc_discount_from_lines = Decimal("0.00")
        sum_line_net = sum203
        tax_total = Decimal("0")
        for it in items:
            if "_idx" in it:
                base = it["_base203"]
                rate = it.get("ddv_stopnja", Decimal("0"))
                it["cena_netto"] = base
                it["vrednost"] = base
                it["_pre_doc_net"] = base
                it["net"] = base
                it["rabata"] = Decimal("0")
                it["rabata_pct"] = Decimal("0.00")
                it["ddv"] = calculate_vat(base, rate)
                tax_total += it["ddv"]
        tax_total = tax_total.quantize(DEC2, ROUND_HALF_UP)
        lines_by_rate = lines_by_rate_info
    else:
        sum_line_net = net_total

    global _INFO_DISCOUNTS
    _INFO_DISCOUNTS = mode == "info"
    for ln in line_logs:
        line_net_used = ln["moa203"] if _INFO_DISCOUNTS else ln["net_std"]
        log.debug(
            "line_idx=%s, moa203=%s, line_net_used=%s, doc_added=%s, "
            "carried_doc_disc=%s",
            ln["idx"],
            ln["moa203"],
            line_net_used,
            ln.get("doc_added", Decimal("0")),
            ln.get("carried_doc_disc", Decimal("0")),
        )

    gross_before_doc = _dec2(sum_line_net + tax_total)
    if hdr_net is None:
        header_net_for_doc = sum203 if hdr125 is None else hdr125
    else:
        header_net_for_doc = hdr_net

    net_diff: Decimal | None = None
    net_mismatch = False
    net_warn = False

    if header_net_for_doc is not None:
        net_diff = abs(header_net_for_doc - sum_lines_net)
        net_mismatch = net_diff > NET_TOL
        net_warn = TOL < net_diff <= NET_TOL
    header_totals_match = (
        header_net_for_doc is not None
        and hdr9 is not None
        and -TOL <= (sum_line_net - header_net_for_doc) <= TOL
        and -TOL <= (gross_before_doc - hdr9) <= TOL
    )

    # ───────── DOCUMENT ALLOWANCES & CHARGES ─────────
    discount_set = set(discount_codes or DEFAULT_DOC_DISCOUNT_CODES)
    if header_totals_match:
        net_after_doc = sum_line_net
        doc_allow_header = Decimal("0")
        doc_charge_total = Decimal("0")
        doc_discount_from_lines = Decimal("0")
    else:
        net_after_doc, doc_allow_header, doc_charge_total = (
            _apply_doc_allowances_sequential(
                sum_line_net,
                root,
                discount_codes=discount_set,
                charge_codes=set(DEFAULT_DOC_CHARGE_CODES),
            )
        )
    if header_totals_match:
        doc_allow_total = Decimal("0")
    else:
        doc_allow_total = doc_allow_header + doc_discount_from_lines
        if doc_allow_total == 0:
            extra_doc_allow = sum_moa(
                root, list(discount_set), doc_level_only=True, tax_amount=None
            )
            if extra_doc_allow != 0:
                # Header-level MOA discounts without explicit S_ALC should reduce
                # the net amount, so treat the detected value as an allowance.
                doc_allow_total = -extra_doc_allow

    doc_adjust_total = doc_allow_total + doc_charge_total

    line_indices = [idx for idx, it in enumerate(items) if "_pre_doc_net" in it]
    base_total = sum((items[idx]["_pre_doc_net"] for idx in line_indices), Decimal("0"))

    allocations: dict[int, Decimal] = {}
    if base_total != 0 and doc_adjust_total != 0 and line_indices:
        running = Decimal("0")
        for idx in line_indices:
            share = items[idx]["_pre_doc_net"] / base_total
            alloc = _dec2(doc_adjust_total * share)
            allocations[idx] = alloc
            running += alloc

        remainder = _dec2(doc_adjust_total - running)
        if remainder != 0:
            idx_biggest = max(line_indices, key=lambda i: abs(items[i]["_pre_doc_net"]))
            allocations[idx_biggest] = allocations.get(idx_biggest, Decimal("0")) + remainder

    net_total = Decimal("0")
    tax_total = Decimal("0")
    lines_by_rate = {}
    for idx, it in enumerate(items):
        if "_pre_doc_net" not in it:
            continue
        alloc = allocations.get(idx, Decimal("0"))
        if alloc != 0:
            it["doc_discount_alloc"] = alloc
        new_net = _dec2(it["vrednost"] + alloc)
        it["vrednost"] = new_net
        it["net"] = new_net
        qty = it.get("kolicina", Decimal("0"))
        if qty:
            it["cena_netto"] = (new_net / qty).quantize(DEC4, rounding=ROUND_HALF_UP)

        rate = it.get("ddv_stopnja", Decimal("0"))
        vat_val = calculate_vat(new_net, rate) if rate else Decimal("0")
        it["ddv"] = vat_val

        net_total = (net_total + new_net).quantize(DEC2, ROUND_HALF_UP)
        tax_total = (tax_total + vat_val).quantize(DEC2, ROUND_HALF_UP)
        if rate:
            lines_by_rate[rate] = lines_by_rate.get(rate, Decimal("0")) + new_net

    if doc_allow_total != 0:
        items.append(
            {
                "sifra_dobavitelja": "_DOC_",
                "naziv": "Popust na ravni računa",
                "kolicina": Decimal("1"),
                "enota": "",
                "cena_bruto": doc_allow_total,
                "cena_netto": doc_allow_total,
                "rabata": -doc_allow_total,
                "rabata_pct": Decimal("100.00"),
                "vrednost": doc_allow_total,
                "ddv": Decimal("0"),
                "is_gratis": False,
            }
        )

    if doc_charge_total != 0:
        items.append(
            {
                "sifra_dobavitelja": "DOC_CHG",
                "naziv": "Strošek na ravni računa",
                "kolicina": Decimal("1"),
                "enota": "",
                "cena_bruto": doc_charge_total,
                "cena_netto": doc_charge_total,
                "rabata": Decimal("0"),
                "rabata_pct": Decimal("0.00"),
                "vrednost": doc_charge_total,
                "ddv": Decimal("0"),
                "is_gratis": False,
            }
        )

    net_total = _dec2(net_total)
    preferred_net, preferred_vat, preferred_gross, totals_meta = (
        extract_header_totals_preferred(
            root, net_fallback=net_total, tax_fallback=tax_total
        )
    )

    sum_vat = _dec2(tax_total)
    vat_total = sum_vat
    if preferred_vat != 0:
        diff_vat = preferred_vat - sum_vat
        if abs(diff_vat) >= DEC2 and line_indices:
            idx_biggest_vat = max(
                line_indices, key=lambda i: abs(items[i].get("ddv", Decimal("0")))
            )
            items[idx_biggest_vat]["ddv"] = _dec2(
                items[idx_biggest_vat].get("ddv", Decimal("0")) + diff_vat
            )
            tax_total = _dec2(
                sum(items[i].get("ddv", Decimal("0")) for i in line_indices)
            )
        vat_total = preferred_vat

    net_total = preferred_net
    gross_calc = (net_total + vat_total).quantize(DEC2, ROUND_HALF_UP)
    gross_attr = preferred_gross
    diff_gross = abs(gross_calc - gross_attr)
    ok = diff_gross <= DEC2
    warn_gross = diff_gross > DEC2
    if warn_gross and _mode_override is None:
        buf = io.BytesIO(LET.tostring(root))
        alt_mode = "real" if _INFO_DISCOUNTS else "info"
        df_alt, ok_alt = parse_eslog_invoice(
            buf, discount_codes, _mode_override=alt_mode
        )
        gross_alt = df_alt.attrs.get("gross_calc", gross_attr)
        diff_alt = abs(gross_alt - gross_attr)
        if diff_alt < diff_gross:
            return df_alt, ok_alt

    if net_mismatch:
        ok = False
    elif net_warn:
        log.warning(
            "Header net total differs from line sum by %s (tolerated as rounding)",
            net_diff,
        )

    gross_reference = (
        gross_attr
        if str(totals_meta.get("gross_source", "")).startswith("MOA")
        else Decimal("0")
    )
    final_diff = diff_gross
    if gross_reference != 0:
        gross_check = (net_total + vat_total).quantize(DEC2, ROUND_HALF_UP)
        final_diff = abs(gross_check - gross_reference)
        if final_diff <= DEC2:
            ok = True
        elif warn_gross:
            log.warning(
                "Invoice total mismatch: MOA 9/38/388 %s vs calculated %s",
                gross_reference,
                gross_check,
            )
    else:
        final_diff = Decimal("0")

    mode_result = "error" if not ok else mode
    _INFO_DISCOUNTS = mode_result == "info"

    # Debug: remove once sanity checks pass
    log.info(
        "hdr125=%s, sum203=%s, sum_line_net_std=%s, hdr260_present=%s, "
        "mode_result=%s",
        _dec2(hdr125) if hdr125 is not None else None,
        sum203,
        sum_line_net_std,
        hdr260_present,
        mode_result,
    )

    for it in items:
        it.pop("_idx", None)
        it.pop("_base203", None)
        it.pop("_net_std", None)
        it.pop("_pre_doc_net", None)

    df = pd.DataFrame(items)
    df.attrs["vat_mismatch"] = vat_mismatch
    df.attrs["net_mismatch"] = net_mismatch
    df.attrs["net_warning"] = net_warn
    df.attrs["info_discounts"] = _INFO_DISCOUNTS
    df.attrs["gross_calc"] = gross_attr
    df.attrs["gross_mismatch"] = gross_reference != 0 and final_diff > DEC2
    df.attrs["header_totals_meta"] = totals_meta
    df.attrs["mode"] = mode_result
    if "sifra_dobavitelja" in df.columns and not df["sifra_dobavitelja"].any():
        df["sifra_dobavitelja"] = supplier_code
    if not df.empty:
        df.sort_values(
            ["sifra_dobavitelja", "naziv"], inplace=True, ignore_index=True
        )

    return df, ok


INFO_LINE_CODES = {"_DOC_", "DOC_CHG"}


def build_invoice_model(
    tree: LET._Element | LET._ElementTree,
 ) -> SimpleNamespace:
    """Construct and return basic invoice totals model.

    The helper serializes ``tree`` and feeds it through
    :func:`parse_eslog_invoice` which performs all allowance and VAT
    aggregation.  Totals are computed by summing the resulting line model.
    """

    if hasattr(tree, "getroot"):
        root = tree.getroot()
    else:
        root = tree

    buf = io.BytesIO(LET.tostring(root))
    df, ok = parse_eslog_invoice(buf)

    if "sifra_dobavitelja" in df.columns:
        info_mask = df["sifra_dobavitelja"].isin(INFO_LINE_CODES)
        df_main = df[~info_mask]
    else:
        df_main = df
    net_total = (
        df_main["vrednost"].sum() if "vrednost" in df_main.columns else Decimal("0")
    )
    vat_total = (
        df_main["ddv"].sum() if "ddv" in df_main.columns else Decimal("0")
    )
    gross_total = net_total + vat_total
    mismatch = (not ok) or bool(df.attrs.get("vat_mismatch", False))

    return SimpleNamespace(
        net_total=net_total,
        vat_total=vat_total,
        gross_total=gross_total,
        mismatch=mismatch,
    )


def parse_invoice_totals(
    root_or_tree: LET._Element | LET._ElementTree,
) -> dict[str, Decimal | bool | str]:
    """Return aggregated invoice totals and related metadata."""

    xml_root = root_or_tree
    try:
        if hasattr(root_or_tree, "getroot"):
            xml_root = root_or_tree.getroot()
    except Exception:
        pass

    _force_ns_for_doc(xml_root)
    log.info("eslog NS[e]=%s", NS.get("e"))

    buf = io.BytesIO(LET.tostring(xml_root))
    df, _ = parse_eslog_invoice(buf)

    if "sifra_dobavitelja" in df.columns:
        info_mask = df["sifra_dobavitelja"].isin(INFO_LINE_CODES)
        df_main = df[~info_mask]
    else:
        df_main = df
    net_total = (
        _dec2(df_main["vrednost"].sum())
        if "vrednost" in df_main.columns
        else Decimal("0")
    )
    vat_total = (
        _dec2(df_main["ddv"].sum()) if "ddv" in df_main.columns else Decimal("0")
    )

    preferred_net, preferred_vat, preferred_gross, totals_meta = (
        extract_header_totals_preferred(
            xml_root, net_fallback=net_total, tax_fallback=vat_total
        )
    )

    net_total = preferred_net
    vat_total = preferred_vat
    gross_total = preferred_gross
    calc_gross = _dec2(net_total + vat_total)
    gross_reference = (
        gross_total
        if str(totals_meta.get("gross_source", "")).startswith("MOA")
        else Decimal("0")
    )

    mismatch = bool(df.attrs.get("vat_mismatch", False))
    if gross_reference != 0 and abs(calc_gross - gross_reference) > DEC2:
        mismatch = True
        log.warning(
            "Invoice total mismatch: MOA 9/38/388 %s vs calculated %s",
            gross_reference,
            calc_gross,
        )

    meta: dict[str, Decimal | bool | str] = {
        "net": net_total,
        "vat": vat_total,
        "gross": gross_total,
        "mismatch": mismatch,
    }

    if not meta.get("supplier_name"):
        meta["supplier_name"] = (
            _first_text(
                xml_root,
                [
                    ".//e:G_SG2[e:S_NAD/e:D_3035='SE']/e:S_NAD/"
                    "e:C_C080/e:D_3036",
                    ".//e:G_SG2[e:S_NAD/e:D_3035='SE']/e:S_NAD/"
                    "e:C_C082/e:D_3039",
                    ".//e:G_SG2[e:S_NAD/e:D_3035='SU']/e:S_NAD/"
                    "e:C_C080/e:D_3036",
                    ".//e:G_SG2[e:S_NAD/e:D_3035='SU']/e:S_NAD/"
                    "e:C_C082/e:D_3039",
                ],
            )
            or ""
        )

    if not meta.get("service_date"):
        meta["service_date"] = (
            _first_text(
                xml_root,
                [
                    ".//e:S_DTM[e:C_C507/e:D_2005='35']/e:C_C507/e:D_2380",
                    ".//e:S_DTM[e:C_C507/e:D_2005='137']/e:C_C507/e:D_2380",
                ],
            )
            or meta.get("delivery_date")
            or meta.get("document_date")
            or ""
        )

    return meta


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
      • gross_total: Decimal (vsota zaokroženih neto in DDV zneskov po
        posameznih vrsticah)
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
            discount_total = (
                Decimal("0")
                if df_items.attrs.get("info_discounts")
                else -sum_moa(root, DEFAULT_DOC_DISCOUNT_CODES)
            )

        gross_total = (
            _dec2((df_items["vrednost"] + df_items["ddv"]).sum())
            if not df_items.empty
            else Decimal("0")
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
                "ddv": df_items["ddv"],
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
