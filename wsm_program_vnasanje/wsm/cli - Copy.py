# File: wsm/cli.py
# -*- coding: utf-8 -*-
"""
WSM obdelava računov – pravilen dobavitelj (SU → SE fallback) in auto-detect imen.
"""
from __future__ import annotations

import sys, logging, re
from decimal import Decimal
from pathlib import Path
from tkinter import Tk, filedialog

import pandas as pd

from wsm.parsing.eslog import parse_eslog_invoic, get_supplier_info
from wsm.parsing.money import parse_invoice_total
from wsm.parsing.pdf   import parse_pdf, get_supplier_name_from_pdf
from wsm.utils         import (
    zdruzi_artikle, povezi_z_wsm, export_to_excel,
    load_wsm_data, log_price_history, sanitize_folder_name
)
from wsm.ui.review_links import (
    review_links, _load_supplier_map, _write_supplier_map
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname).1s %(funcName)s:%(lineno)d | %(message)s",
)
log = logging.getLogger(__name__)

def _pick_files() -> list[str]:
    root = Tk(); root.withdraw()
    sel = filedialog.askopenfilenames(
        parent=root, title="Izberi XML in/ali PDF račun",
        filetypes=[("XML ali PDF", "*.xml *.XML *.pdf *.PDF")])
    root.destroy()
    return list(sel)

def main() -> None:
    log.info("Zagon glavnega WSM programa")
    files = sys.argv[1:] or _pick_files()
    if not files:
        log.error("Ni izbranih datotek."); return

    xml = next((f for f in files if f.lower().endswith(".xml")), "")
    pdf = next((f for f in files if f.lower().endswith(".pdf")), "")

    # 1) NETO v glavi
    header_neto: Decimal | None = None
    if xml:
        try:
            header_neto = parse_invoice_total(xml)
            log.info(f"Neto v glavi: {header_neto:.2f} €")
        except Exception as e:
            log.warning(f"parse_invoice_total neuspešen: {e}")

    # 2) postavke
    df_raw = pd.DataFrame()
    suppliers_file = Path("links") / "suppliers.xlsx"
    sup_map = _load_supplier_map(suppliers_file)
    if xml:
        try:
            df_raw = parse_eslog_invoic(xml, sup_map)
        except Exception as e:
            log.warning(f"parse_eslog_invoic neuspešen: {e}")
    if df_raw.empty and pdf:
        try:
            df_raw = parse_pdf(pdf)
        except Exception as e:
            log.warning(f"parse_pdf neuspešen: {e}")
    if df_raw.empty:
        log.error("Ni veljavnih podatkov iz XML ali PDF."); return

    # 3) dobavitelj
    supp, name = ("", "")
    if xml:
        supp, name = get_supplier_info(xml)
    if not name and pdf:
        name = get_supplier_name_from_pdf(pdf)

    if not supp:
        if name:
            supp = re.sub(r"[^A-Za-z0-9]", "", name.upper())[:8]
        else:
            fn = Path(xml or pdf).stem
            candidate = re.sub(r"[^A-Za-z0-9]", "", fn.upper())
            supp = candidate[:8] if candidate else None
        if not supp:
            log.error("Samodejno prepoznavanje dobavitelja ni uspelo. Prosim vnesite kodo dobavitelja.")
            supp = input("Koda dobavitelja: ").strip().upper()

    if not name:
        name = sup_map.get(supp, {}).get('ime', supp)

    if name and (supp not in sup_map or sup_map[supp]['ime'] != name):
        sup_map[supp] = {
            'ime': name,
            'override_H87_to_kg': sup_map.get(supp, {}).get('override_H87_to_kg', False)
        }
        _write_supplier_map(sup_map, suppliers_file)
        log.info(f"✓ Dodano: '{name}' (koda {supp}) v suppliers.xlsx")

    supplier_name = sup_map.get(supp, {}).get('ime', supp)
    safe_name     = sanitize_folder_name(supplier_name)

    # 4) združi + log cen
    df = zdruzi_artikle(df_raw)
    log_price_history(df, Path("links") / safe_name / "price_history.xlsx")

    # 4.1) POSKRBI, DA MAMO PRAVILNE STOLPCE ZA review_links
    if "orig_name" not in df.columns:
        for alt in ("orig_name","naziv_artikla","naziv","name","item_name"):
            if alt in df.columns:
                df = df.rename(columns={alt:"orig_name"})
                break
    if "qty" not in df.columns:
        for alt in ("qty","quantity","kolicina","kol"):
            if alt in df.columns:
                df = df.rename(columns={alt:"qty"})
                break
    if "unit" not in df.columns:
        for alt in ("unit","enota","jed"):
            if alt in df.columns:
                df = df.rename(columns={alt:"unit"})
                break

    # 5) POPUSTI …
    items = df[df["sifra_dobavitelja"] != "_DOC_"]
    neto_items = items["vrednost"].sum()
    line_rebate = items["rabata"].sum()
    doc_rebate = -df.loc[df["sifra_dobavitelja"] == "_DOC_", "vrednost"].sum()
    if line_rebate > 0 and doc_rebate > 0:
        doc_rebate = Decimal("0.00")
    total_rebate = line_rebate + doc_rebate

    # Adjust neto_after based on supplier
    if supplier_name.upper() == "MERCATOR D.O.O.":
        neto_after = header_neto if header_neto is not None else neto_items
    else:
        neto_after = (header_neto if header_neto is not None else neto_items) - total_rebate

    log.info("\n============ POVZETEK POPUSTOV ============")
    log.info(f"Skupni NETO brez popustov : {(neto_items + line_rebate):,.2f} €")
    log.info(f"Skupni POPUST            : {total_rebate:,.2f} € "
             f"({line_rebate:,.2f} € vrstični + {doc_rebate:,.2f} € dokument)")
    log.info(f"Skupni NETO s popusti    : {neto_after:,.2f} €")
    log.info("===========================================\n")

    if header_neto:
        znak = "✓" if abs(header_neto - neto_after) < Decimal("0.05") else "✗"
        log.info(f"Glava po popastu: {header_neto:.2f} €  "
                 f"vs. Izračunano: {neto_after:.2f} € → {znak}")

    # 6) POVEZOVANJE WSM KOD …
    WSM, KEY, links_dir = "sifre_wsm.xlsx", "kljucne_besede_wsm_kode.xlsx", Path("links")
    if Path(WSM).exists() and Path(KEY).exists():
        df["naziv"]    = df["orig_name"]
        df["kolicina"] = df["qty"]
        df["enota"]    = df["unit"]

        df = povezi_z_wsm(df, WSM, KEY, links_dir, supp)
        wsm_df, *_ = load_wsm_data(WSM, KEY, links_dir, supp)
        links_file = links_dir / safe_name / f"{supp}_{safe_name}_povezane.xlsx"
        df = review_links(df, wsm_df, links_file, neto_after)

        # Dodana logika za preverjanje veljavnosti WSM šifer
        if "wsm_sifra" in df.columns:
            # Če je WSM šifra enaka imenu dobavitelja ali ni v wsm_df, jo nastavimo na None
            valid_wsm_codes = set(wsm_df.get("wsm_sifra", []))  # Seznam veljavnih WSM šifer
            df["wsm_sifra"] = df["wsm_sifra"].apply(
                lambda x: x if (x and x in valid_wsm_codes and x != supplier_name) else None
            )
            log.info("Preverjene in popravljene WSM šifre za veljavnost.")

        out = links_dir / safe_name
        out.mkdir(parents=True, exist_ok=True)
        export_to_excel(df, "wsm_izvoz.xlsx")
    else:
        log.warning("Ni WSM lookup datotek – preskočen STEP 'povezovanje'.")
        export_to_excel(df, "wsm_izvoz_raw.xlsx")

    log.info("Konec.")

if __name__ == "__main__":
    main()