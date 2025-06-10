# File: wsm/ui/review_links.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import math, re, logging, hashlib
from decimal import Decimal
from pathlib import Path
from typing import Tuple

import pandas as pd
import tkinter as tk
from tkinter import ttk

# Logger setup
log = logging.getLogger(__name__)

# Helper functions
def _fmt(v) -> str:
    """Human-friendly format števil (Decimal / float / int)."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return ""
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    d = d.quantize(Decimal("0.0001"))
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s

_piece = {"kos","kom","stk","st","can","ea","pcs"}
_mass  = {"kg","g","gram","grams","mg","milligram","milligrams"}
_vol   = {"l","ml","cl","dl","dcl"}
_rx_vol  = re.compile(r"([0-9]+[\.,]?[0-9]*)\s*(ml|cl|dl|dcl|l)\b", re.I)
_rx_mass = re.compile(r"(?:teža|masa|weight)?\s*[:\s]?\s*([0-9]+[\.,]?[0-9]*)\s*((?:kgm?)|kgr|g|gr|gram|grams|mg|milligram|milligrams)\b", re.I)
_dec = lambda x: Decimal(x.replace(",","."))

def _norm_unit(q: Decimal, u: str, name: str, override_h87_to_kg: bool = False) -> Tuple[Decimal, str]:
    """Normalize quantity and unit to (kg / L / kos)."""
    log.debug(f"Normalizacija: q={q}, u={u}, name={name}, override_h87_to_kg={override_h87_to_kg}")
    unit_map = {
        "KGM": ("kg", 1),      # Kilograms
        "GRM": ("kg", 0.001),  # Grams (convert to kg)
        "LTR": ("L", 1),       # Liters
        "MLT": ("L", 0.001),   # Milliliters (convert to L)
        "H87": ("kg" if override_h87_to_kg else "kos", 1),  # Piece, override to kg if set
        "EA": ("kos", 1),      # Each (piece)
    }

    if u in unit_map:
        base_unit, factor = unit_map[u]
        q_norm = q * Decimal(str(factor))
        log.debug(f"Enota v unit_map: {u} -> base_unit={base_unit}, factor={factor}, q_norm={q_norm}")
    else:
        u_norm = (u or "").strip().lower()
        if u_norm in _piece:
            base_unit = "kos"
            q_norm = q
        elif u_norm in _mass:
            factor = Decimal("1") if u_norm.startswith("kg") else Decimal("1") / Decimal("1000")
            q_norm = q * factor
            base_unit = "kg"
        elif u_norm in _vol:
            mapping = {"l":1, "ml":1e-3, "cl":1e-2, "dl":1e-1, "dcl":1e-1}
            q_norm = q * Decimal(str(mapping[u_norm]))
            base_unit = "L"
        else:
            name_l = name.lower()
            m_vol = _rx_vol.search(name_l)
            if m_vol:
                val, typ = _dec(m_vol[1]), m_vol[2].lower()
                conv = {"ml": val/1000, "cl": val/100, "dl": val/10, "dcl": val/10, "l": val}[typ]
                q_norm = q * conv
                base_unit = "L"
            else:
                m_mass = _rx_mass.search(name_l)
                if m_mass:
                    val, typ = _dec(m_mass[1]), m_mass[2].lower()
                    conv = val/1000 if typ.startswith(("g", "mg")) else val
                    q_norm = q * conv
                    base_unit = "kg"
                else:
                    q_norm = q
                    base_unit = "kos"
        log.debug(f"Enota ni v unit_map: u_norm={u_norm}, base_unit={base_unit}, q_norm={q_norm}")

    if base_unit == "kos":
        m_weight = re.search(r"(?:teža|masa|weight)?\s*[:\s]?\s*(\d+(?:[.,]\d+)?)\s*(g|dag|kg)\b", name, re.I)
        if m_weight:
            val = Decimal(m_weight.group(1).replace(",", "."))
            unit = m_weight.group(2).lower()
            if unit == "g":
                weight_kg = val / 1000
            elif unit == "dag":
                weight_kg = val / 100
            elif unit == "kg":
                weight_kg = val
            log.debug(f"Teža najdena v imenu: {val} {unit}, pretvorjeno v kg: {weight_kg}")
            return q_norm * weight_kg, "kg"
        
        m_volume = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", name, re.I)
        if m_volume:
            val = Decimal(m_volume.group(1).replace(",", "."))
            unit = m_volume.group(2).lower()
            if unit == "ml":
                volume_l = val / 1000
            elif unit == "l":
                volume_l = val
            log.debug(f"Volumen najden v imenu: {val} {unit}, pretvorjeno v L: {volume_l}")
            if volume_l >= 1:
                return q_norm * volume_l, "L"
            else:
                return q_norm, "kos"
    
    log.debug(f"Končna normalizacija: q_norm={q_norm}, base_unit={base_unit}")
    return q_norm, base_unit

# File handling functions
def _load_supplier_map(sup_file: Path) -> dict[str, dict]:
    """Load supplier map from suppliers.xlsx with improved error handling."""
    log.debug(f"Branje datoteke: {sup_file}")
    if not sup_file.exists():
        log.warning(f"Datoteka {sup_file} ne obstaja.")
        return {}
    try:
        df_sup = pd.read_excel(sup_file, dtype=str)
        log.info(f"Število prebranih dobaviteljev iz {sup_file}: {len(df_sup)}")
        log.debug(f"Stolpci v df_sup: {df_sup.columns.tolist()}")
        log.debug(f"Primer dobaviteljev: {df_sup.head().to_dict(orient='records')}")
        sup_map = {}
        for _, row in df_sup.iterrows():
            sifra = str(row['sifra']).strip()
            ime = str(row['ime']).strip()
            override_value = str(row.get('override_H87_to_kg', 'False')).strip().lower()
            override = override_value in ['true', '1', 'yes']
            sup_map[sifra] = {'ime': ime, 'override_H87_to_kg': override}
            log.debug(f"Dodan v sup_map: sifra={sifra}, ime={ime}, override_value={override_value}, override={override}")
        log.info(f"Uspešno prebran suppliers.xlsx: {list(sup_map.keys())}")
        return sup_map
    except Exception as e:
        log.error(f"Napaka pri branju suppliers.xlsx: {e}")
        return {}

def _write_supplier_map(sup_map: dict, sup_file: Path):
    log.debug(f"Pisanje v datoteko: {sup_file}, vsebina: {sup_map}")
    sup_file.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([
        {"sifra": k, "ime": v['ime'], "override_H87_to_kg": v['override_H87_to_kg']}
        for k, v in sup_map.items()
    ])
    df.to_excel(sup_file, index=False)
    log.info(f"Datoteka uspešno zapisana: {sup_file}")

# Save and close function
def _save_and_close(df, manual_old, wsm_df, links_file, root, supplier_name, supplier_code, sup_map, sup_file):
    log.debug(f"Shranjevanje: supplier_name={supplier_name}, supplier_code={supplier_code}")
    
    # Preverimo prazne sifra_dobavitelja
    empty_sifra = df['sifra_dobavitelja'].isna() | (df['sifra_dobavitelja'] == '')
    if empty_sifra.any():
        log.warning(f"Prazne vrednosti v sifra_dobavitelja za {empty_sifra.sum()} vrstic")
        log.debug(f"Primer vrstic s prazno sifra_dobavitelja: {df[empty_sifra][['naziv', 'sifra_dobavitelja']].head().to_dict()}")
        df.loc[empty_sifra, 'sifra_dobavitelja'] = df.loc[empty_sifra, 'naziv'].apply(
            lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8]
        )
        log.info(f"Generirane začasne šifre za {empty_sifra.sum()} vrstic")
    
    # Posodobi zemljevid dobaviteljev, če se je ime spremenilo
    if supplier_name and sup_map.get(supplier_code, {}).get('ime') != supplier_name:
        sup_map[supplier_code] = {
            'ime': supplier_name,
            'override_H87_to_kg': sup_map.get(supplier_code, {}).get('override_H87_to_kg', False)
        }
        _write_supplier_map(sup_map, sup_file)
    
    # Nastavi indeks za manual_old
    if not manual_old.empty:
        # Odstrani prazne ali neveljavne vrstice
        manual_old = manual_old.dropna(subset=['sifra_dobavitelja', 'naziv'], how='all')
        manual_new = manual_old.set_index(["sifra_dobavitelja"])
        log.info(f"Število prebranih povezav iz manual_old: {len(manual_old)}")
        log.debug(f"Primer povezav iz manual_old: {manual_old.head().to_dict()}")
    else:
        manual_new = pd.DataFrame(columns=["sifra_dobavitelja", "naziv", "wsm_sifra", "dobavitelj"]).set_index(["sifra_dobavitelja"])
        log.info("Manual_old je prazen, ustvarjam nov DataFrame")
    
    # Ustvari df_links z istim indeksom
    df_links = df.set_index(["sifra_dobavitelja"])[["naziv", "wsm_sifra", "dobavitelj"]]
    
    # Posodobi obstoječe elemente (dovoli tudi brisanje povezav)
    manual_new.loc[df_links.index, ["naziv", "wsm_sifra", "dobavitelj"]] = df_links
    
    # Dodaj nove elemente, ki niso v manual_new
    new_items = df_links[~df_links.index.isin(manual_new.index)]
    manual_new = pd.concat([manual_new, new_items])
    
    # Ponastavi indeks, da vrneš stolpce
    manual_new = manual_new.reset_index()
    
    # Shrani v Excel
    log.info(f"Shranjujem {len(manual_new)} povezav v {links_file}")
    log.debug(f"Primer shranjenih povezav: {manual_new.head().to_dict()}")
    try:
        manual_new.to_excel(links_file, index=False)
        log.info(f"Uspešno shranjeno v {links_file}")
    except Exception as e:
        log.error(f"Napaka pri shranjevanju v {links_file}: {e}")
    
    root.quit()

# Main GUI function
def review_links(df: pd.DataFrame, wsm_df: pd.DataFrame, links_file: Path, invoice_total: Decimal) -> pd.DataFrame:
    df = df.copy()
    supplier_code = links_file.stem.split("_")[0]
    suppliers_file = Path("links") / "suppliers.xlsx"
    log.debug(f"Pot do suppliers.xlsx: {suppliers_file}")
    sup_map = _load_supplier_map(suppliers_file)
    
    log.info(f"Supplier code extracted: {supplier_code}")
    supplier_info = sup_map.get(supplier_code, {})
    default_name = supplier_info.get('ime', supplier_code)
    override_h87_to_kg = supplier_info.get('override_H87_to_kg', False)
    log.info(f"Default name retrieved: {default_name}")
    log.debug(f"Supplier info: {supplier_info}")
    log.info(f"Override H87 to kg: {override_h87_to_kg}")
    
    try:
        manual_old = pd.read_excel(links_file, dtype=str)
        log.info(f"Processing complete")
        log.info(f"Število prebranih povezav iz {links_file}: {len(manual_old)}")
        log.debug(f"Primer povezav iz {links_file}: {manual_old.head().to_dict()}")
        empty_sifra_old = manual_old['sifra_dobavitelja'].isna() | (manual_old['sifra_dobavitelja'] == '')
        if empty_sifra_old.any():
            log.warning(f"Prazne vrednosti v sifra_dobavitelja v manual_old za {empty_sifra_old.sum()} vrstic")
            manual_old.loc[empty_sifra_old, 'sifra_dobavitelja'] = manual_old.loc[empty_sifra_old, 'naziv'].apply(
                lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8]
            )
            log.info(f"Generirane začasne šifre za {empty_sifra_old.sum()} vrstic v manual_old")
    except Exception as e:
        manual_old = pd.DataFrame(columns=["sifra_dobavitelja","naziv","wsm_sifra","dobavitelj"])
        log.debug(f"Manual_old ni obstajal ali napaka pri branju: {e}, ustvarjam prazen DataFrame")
    
    existing_names = sorted({n for n in manual_old.get("dobavitelj", []) if isinstance(n, str) and n.strip()})
    supplier_name = default_name
    if supplier_name and supplier_name not in existing_names:
        existing_names.insert(0, supplier_name)
    supplier_name = existing_names[0] if existing_names else supplier_code
    df["dobavitelj"] = supplier_name
    log.debug(f"Supplier name nastavljen na: {supplier_name}")
    
    # Generate sifra_dobavitelja for empty cases before lookup
    empty_sifra = df['sifra_dobavitelja'].isna() | (df['sifra_dobavitelja'] == '')
    if empty_sifra.any():
        df.loc[empty_sifra, 'sifra_dobavitelja'] = df.loc[empty_sifra, 'naziv'].apply(
            lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8]
            )
        log.info(f"Generirane začasne šifre za {empty_sifra.sum()} vrstic v df")
    
    # Create a dictionary for quick lookup
    old_map_dict = manual_old.set_index(["sifra_dobavitelja"])["wsm_sifra"].to_dict()
    
    df["wsm_sifra"] = df.apply(
        lambda r: old_map_dict.get((r["sifra_dobavitelja"]), pd.NA),
        axis=1
    )
    df["wsm_naziv"] = df["wsm_sifra"].map(wsm_df.set_index("wsm_sifra")["wsm_naziv"])
    df["status"] = df["wsm_sifra"].notna().map({True:"POVEZANO", False: pd.NA})
    log.debug(f"df po inicializaciji: {df.head().to_dict()}")
    
    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    doc_discount_total = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
    df["cena_pred_rabatom"] = (df["vrednost"] + df["rabata"]) / df["kolicina"]
    df["cena_po_rabatu"] = df["vrednost"] / df["kolicina"]
    df["rabata_pct"] = df.apply(
        lambda r: ((r["rabata"]/(r["vrednost"]+r["rabata"]))
                   *Decimal("100")).quantize(Decimal("0.01"))
        if (r["vrednost"]+r["rabata"]) else Decimal("0.00"),
        axis=1
    )
    df["total_net"] = df["vrednost"]
    df["kolicina_norm"], df["enota_norm"] = zip(*[
        _norm_unit(Decimal(str(q)), u, n, override_h87_to_kg)
        for q,u,n in zip(df["kolicina"], df["enota"], df["naziv"])
    ])
    df["kolicina_norm"] = df["kolicina_norm"].astype(float)
    log.debug(f"df po normalizaciji: {df.head().to_dict()}")

    # Adjust document discount if summed lines differ slightly from invoice total
    calculated_total = df["total_net"].sum() + doc_discount_total
    diff = invoice_total - calculated_total
    if abs(diff) <= Decimal("0.02") and diff != 0:
        log.debug(
            f"Prilagajam dokumentarni popust za razliko {diff}: "
            f"{doc_discount_total} -> {doc_discount_total + diff}"
        )
        doc_discount_total += diff
        if not df_doc.empty:
            df_doc.loc[df_doc.index, "vrednost"] += diff
            df_doc.loc[df_doc.index, "cena_bruto"] += abs(diff)
            df_doc.loc[df_doc.index, "rabata"] += abs(diff)

    root = tk.Tk()
    root.title(f"Ročna revizija – {supplier_name}")
    # Start in fullscreen; press Esc to exit
    root.attributes("-fullscreen", True)
    root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
    cols = ["naziv","kolicina_norm","enota_norm","rabata_pct","cena_pred_rabatom",
            "cena_po_rabatu","total_net","wsm_naziv","dobavitelj"]
    heads= ["Naziv artikla","Količina","Enota","Rabat (%)","Net. pred rab.",
            "Net. po rab.","Skupna neto","WSM naziv","Dobavitelj"]
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=27)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    
    for c,h in zip(cols, heads):
        tree.heading(c, text=h)
        width = 300 if c == "naziv" else (80 if c == "enota_norm" else 120)
        tree.column(c, width=width, anchor="w")
    for i, row in df.iterrows():
        vals = [_fmt(row[c]) if isinstance(row[c], (Decimal,float,int)) else ("" if pd.isna(row[c]) else str(row[c]))
                for c in cols]
        tree.insert("", "end", iid=str(i), values=vals)
    tree.focus("0")
    tree.selection_set("0")

    # Povzetek skupnih neto cen po WSM šifrah
    summary_frame = tk.Frame(root)
    summary_frame.pack(fill="both", expand=True, pady=10)
    tk.Label(summary_frame, text="Povzetek po WSM šifrah", font=("Arial", 12, "bold")).pack()
    
    summary_cols = [
        "wsm_sifra",
        "wsm_naziv",
        "neto_brez_popusta",
        "rabata",
        "vrednost",
        "kolicina_norm",
        "enota_norm",
        "avg_price_per_unit",
    ]
    summary_heads = [
        "WSM Šifra",
        "WSM Naziv",
        "Neto brez popusta",
        "Rabat",
        "Neto po rabatu",
        "Skupna količina",
        "Enota",
        "Povp. cena/enoto",
    ]
    summary_tree = ttk.Treeview(summary_frame, columns=summary_cols, show="headings", height=5)
    vsb_summary = ttk.Scrollbar(summary_frame, orient="vertical", command=summary_tree.yview)
    summary_tree.configure(yscrollcommand=vsb_summary.set)
    vsb_summary.pack(side="right", fill="y")
    summary_tree.pack(side="left", fill="both", expand=True)
    
    for c, h in zip(summary_cols, summary_heads):
        summary_tree.heading(c, text=h)
        width = 80 if c == "enota_norm" else 150
        summary_tree.column(c, width=width, anchor="w")

    def _update_summary():
        for item in summary_tree.get_children():
            summary_tree.delete(item)
        required = {'wsm_sifra', 'vrednost', 'rabata', 'kolicina_norm', 'enota_norm'}
        if required.issubset(df.columns):
            summary_df = (
                df[df['wsm_sifra'].notna()]
                .groupby('wsm_sifra')
                .agg({
                    'vrednost': 'sum',
                    'rabata': 'sum',
                    'kolicina_norm': 'sum',
                    'enota_norm': 'first',
                })
                .reset_index()
            )

            summary_df['neto_brez_popusta'] = summary_df['vrednost'] + summary_df['rabata']
            summary_df['wsm_naziv'] = summary_df['wsm_sifra'].map(
                wsm_df.set_index('wsm_sifra')['wsm_naziv']
            )

            def calculate_avg_price(row):
                try:
                    total = Decimal(str(row['vrednost']))
                    qty = Decimal(str(row['kolicina_norm']))
                    if pd.isna(total) or pd.isna(qty) or qty == 0:
                        return Decimal('0')
                    return (total / qty).quantize(Decimal('0.0001'))
                except Exception as e:
                    log.error(f"Napaka pri izračunu povprečne cene za vrstico {row}: {e}")
                    return Decimal('0')

            summary_df['avg_price_per_unit'] = summary_df.apply(calculate_avg_price, axis=1)

            for _, row in summary_df.iterrows():
                vals = [
                    row['wsm_sifra'],
                    row['wsm_naziv'],
                    _fmt(row['neto_brez_popusta']),
                    _fmt(row['rabata']),
                    _fmt(row['vrednost']),
                    _fmt(row['kolicina_norm']),
                    row['enota_norm'],
                    _fmt(row['avg_price_per_unit']),
                ]
                summary_tree.insert("", "end", values=vals)
            log.debug(f"Povzetek posodobljen: {len(summary_df)} WSM šifer")

    # Skupni zneski pod povzetkom
    total_frame = tk.Frame(root)
    total_frame.pack(fill="x", pady=5)

    linked_total = df[df['wsm_sifra'].notna()]['total_net'].sum()
    # "Skupaj ostalo" naj zajema tudi morebitni dokumentarni popust,
    # ki je izločen iz df in shranjen kot ``doc_discount_total``.
    unlinked_total = df[df['wsm_sifra'].isna()]['total_net'].sum() + doc_discount_total
    # Skupni seštevek mora biti vsota "povezano" in "ostalo"
    total_sum = linked_total + unlinked_total
    match_symbol = "✓" if abs(total_sum - invoice_total) <= Decimal("0.01") else "✗"
    
    tk.Label(total_frame, text=f"Skupaj povezano: {_fmt(linked_total)} € + Skupaj ostalo: {_fmt(unlinked_total)} € = Skupni seštevek: {_fmt(total_sum)} € | Skupna vrednost računa: {_fmt(invoice_total)} € {match_symbol}", 
            font=('Arial', 10, 'bold'), name='total_sum').pack(side='left', padx=10)

    def _update_totals():
        linked_total = df[df['wsm_sifra'].notna()]['total_net'].sum()
        unlinked_total = df[df['wsm_sifra'].isna()]['total_net'].sum() + doc_discount_total
        total_sum = linked_total + unlinked_total
        match_symbol = "✓" if abs(total_sum - invoice_total) <= Decimal("0.01") else "✗"
        total_frame.children['total_sum'].config(text=f"Skupaj povezano: {_fmt(linked_total)} € + Skupaj ostalo: {_fmt(unlinked_total)} € = Skupni seštevek: {_fmt(total_sum)} € | Skupna vrednost računa: {_fmt(invoice_total)} € {match_symbol}")

    bottom = tk.Frame(root)
    bottom.pack(fill="x", padx=8, pady=6)
    custom = tk.Frame(bottom)
    custom.pack(fill="x")
    tk.Label(custom, text="Vpiši / izberi WSM naziv:").pack(side="left")
    entry = tk.Entry(custom)
    entry.pack(side="left", fill="x", expand=True, padx=(4,0))
    lb = tk.Listbox(custom, height=6)

    save_btn = tk.Button(
        bottom, text="Shrani & zapri", width=14,
        command=lambda e=None: _save_and_close(
            df, manual_old, wsm_df, links_file, root,
            supplier_name, supplier_code, sup_map, suppliers_file
        )
    )
    save_btn.pack(side="right", padx=(6,0))
    
    exit_btn = tk.Button(
        bottom, text="Izhod", width=14,
        command=root.quit
    )
    exit_btn.pack(side="right", padx=(6,0))
    
    root.bind("<F10>", lambda e: _save_and_close(df, manual_old, wsm_df, links_file, root,
                                              supplier_name, supplier_code,
                                              sup_map, suppliers_file))

    nazivi = wsm_df["wsm_naziv"].dropna().tolist()
    n2s = dict(zip(wsm_df["wsm_naziv"], wsm_df["wsm_sifra"]))

    def _start_edit(_=None):
        if not tree.focus():
            return "break"
        entry.delete(0, "end")
        lb.pack_forget()
        entry.focus_set()
        return "break"

    def _suggest(evt=None):
        if evt and evt.keysym in {"Return","Escape","Up","Down","Tab","Right","Left"}:
            return
        txt = entry.get().strip().lower()
        lb.delete(0, "end")
        if not txt:
            lb.pack_forget()
            return
        matches = [n for n in nazivi if txt in n.lower()]
        if matches:
            lb.pack(fill="x")
            for m in matches:
                lb.insert("end", m)
            lb.selection_set(0)
            lb.activate(0)
            lb.see(0)
        else:
            lb.pack_forget()

    def _init_listbox(evt=None):
        """Give focus to the listbox and handle initial navigation."""
        if lb.winfo_ismapped():
            lb.focus_set()
            if not lb.curselection():
                lb.selection_set(0)
                lb.activate(0)
                lb.see(0)
            if evt and evt.keysym == "Down":
                _nav_list(evt)
        return "break"

    def _nav_list(evt):
        cur = lb.curselection()[0] if lb.curselection() else -1
        nxt = cur + 1 if evt.keysym == "Down" else cur - 1
        nxt = max(0, min(lb.size()-1, nxt))
        lb.selection_clear(0, "end")
        lb.selection_set(nxt)
        lb.activate(nxt)
        lb.see(nxt)
        return "break"

    def _confirm(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        choice = lb.get(lb.curselection()[0]) if lb.curselection() else entry.get().strip()
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = choice
        df.at[idx, "wsm_sifra"] = n2s.get(choice, pd.NA)
        df.at[idx, "status"] = "POVEZANO"
        df.at[idx, "dobavitelj"] = supplier_name
        if pd.isna(df.at[idx, "sifra_dobavitelja"]) or df.at[idx, "sifra_dobavitelja"] == "":
            df.at[idx, "sifra_dobavitelja"] = hashlib.md5(str(df.at[idx, "naziv"]).encode()).hexdigest()[:8]
        new_vals = [_fmt(df.at[idx, c]) if isinstance(df.at[idx, c], (Decimal,float,int)) else
                    ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c])) for c in cols]
        tree.item(sel_i, values=new_vals)
        log.debug(f"Potrjeno: idx={idx}, wsm_naziv={choice}, wsm_sifra={df.at[idx, 'wsm_sifra']}, sifra_dobavitelja={df.at[idx, 'sifra_dobavitelja']}")
        _update_summary()  # Update summary after confirming
        _update_totals()   # Update totals after confirming
        entry.delete(0, "end")
        lb.pack_forget()
        tree.focus_set()
        next_i = tree.next(sel_i)
        if next_i:
            tree.selection_set(next_i)
            tree.focus(next_i)
            tree.see(next_i)
        return "break"

    def _clear_wsm_connection(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = pd.NA
        df.at[idx, "wsm_sifra"] = pd.NA
        df.at[idx, "status"] = pd.NA
        new_vals = [_fmt(df.at[idx, c]) if isinstance(df.at[idx, c], (Decimal,float,int)) else
                    ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c])) for c in cols]
        tree.item(sel_i, values=new_vals)
        log.debug(f"Povezava odstranjena: idx={idx}, wsm_naziv=NaN, wsm_sifra=NaN")
        _update_summary()  # Update summary after clearing
        _update_totals()   # Update totals after clearing
        tree.focus_set()
        return "break"

    def _tree_nav_up(_=None):
        """Select previous row and ensure it is visible."""
        prev_item = tree.prev(tree.focus()) or tree.focus()
        tree.selection_set(prev_item)
        tree.focus(prev_item)
        tree.see(prev_item)
        return "break"

    def _tree_nav_down(_=None):
        """Select next row and ensure it is visible."""
        next_item = tree.next(tree.focus()) or tree.focus()
        tree.selection_set(next_item)
        tree.focus(next_item)
        tree.see(next_item)
        return "break"

    # Vezave za tipke na tree
    tree.bind("<Return>", _start_edit)
    tree.bind("<BackSpace>", _clear_wsm_connection)
    tree.bind("<Up>", _tree_nav_up)
    tree.bind("<Down>", _tree_nav_down)

    # Vezave za entry in lb
    entry.bind("<KeyRelease>", _suggest)
    entry.bind("<Down>", _init_listbox)
    entry.bind("<Tab>", _init_listbox)
    entry.bind("<Right>", _init_listbox)
    entry.bind("<Return>", _confirm)
    entry.bind("<Escape>", lambda e: (lb.pack_forget(), entry.delete(0, "end"), tree.focus_set(), "break"))
    lb.bind("<Return>", _confirm)
    lb.bind("<Double-Button-1>", _confirm)
    lb.bind("<Down>", _nav_list)
    lb.bind("<Up>", _nav_list)

    # Prvič osveži
    _update_summary()
    _update_totals()

    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass

    return pd.concat([df, df_doc], ignore_index=True)
