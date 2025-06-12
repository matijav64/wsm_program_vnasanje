# File: wsm/ui/review_links.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import math, re, logging, hashlib, json
from decimal import Decimal
from wsm.parsing.money import detect_round_step
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


_piece = {"kos", "kom", "stk", "st", "can", "ea", "pcs"}
_mass = {"kg", "g", "gram", "grams", "mg", "milligram", "milligrams"}
_vol = {"l", "ml", "cl", "dl", "dcl"}
_rx_vol = re.compile(r"([0-9]+[\.,]?[0-9]*)\s*(ml|cl|dl|dcl|l)\b", re.I)
_rx_mass = re.compile(
    r"(?:teža|masa|weight)?\s*[:\s]?\s*([0-9]+[\.,]?[0-9]*)\s*((?:kgm?)|kgr|g|gr|gram|grams|mg|milligram|milligrams)\b",
    re.I,
)
_dec = lambda x: Decimal(x.replace(",", "."))


def _norm_unit(
    q: Decimal, u: str, name: str, override_h87_to_kg: bool = False
) -> Tuple[Decimal, str]:
    """Normalize quantity and unit to (kg / L / kos)."""
    log.debug(
        f"Normalizacija: q={q}, u={u}, name={name}, override_h87_to_kg={override_h87_to_kg}"
    )
    unit_map = {
        "KGM": ("kg", 1),  # Kilograms
        "GRM": ("kg", 0.001),  # Grams (convert to kg)
        "LTR": ("L", 1),  # Liters
        "MLT": ("L", 0.001),  # Milliliters (convert to L)
        "H87": (
            "kg" if override_h87_to_kg else "kos",
            1,
        ),  # Piece, override to kg if set
        "EA": ("kos", 1),  # Each (piece)
    }

    if u in unit_map:
        base_unit, factor = unit_map[u]
        q_norm = q * Decimal(str(factor))
        log.debug(
            f"Enota v unit_map: {u} -> base_unit={base_unit}, factor={factor}, q_norm={q_norm}"
        )
    else:
        u_norm = (u or "").strip().lower()
        if u_norm in _piece:
            base_unit = "kos"
            q_norm = q
        elif u_norm in _mass:
            if u_norm.startswith("kg"):
                factor = Decimal("1")
            elif u_norm.startswith("mg") or u_norm.startswith("milligram"):
                factor = Decimal("1") / Decimal("1000000")
            else:
                factor = Decimal("1") / Decimal("1000")
            q_norm = q * factor
            base_unit = "kg"
        elif u_norm in _vol:
            mapping = {"l": 1, "ml": 1e-3, "cl": 1e-2, "dl": 1e-1, "dcl": 1e-1}
            q_norm = q * Decimal(str(mapping[u_norm]))
            base_unit = "L"
        else:
            name_l = name.lower()
            m_vol = _rx_vol.search(name_l)
            if m_vol:
                val, typ = _dec(m_vol[1]), m_vol[2].lower()
                conv = {
                    "ml": val / 1000,
                    "cl": val / 100,
                    "dl": val / 10,
                    "dcl": val / 10,
                    "l": val,
                }[typ]
                q_norm = q * conv
                base_unit = "L"
            else:
                m_mass = _rx_mass.search(name_l)
                if m_mass:
                    val, typ = _dec(m_mass[1]), m_mass[2].lower()
                    if typ.startswith("kg"):
                        conv = val
                    elif typ.startswith("mg") or typ.startswith("milligram"):
                        conv = val / 1000000
                    else:
                        conv = val / 1000
                    q_norm = q * conv
                    base_unit = "kg"
                else:
                    q_norm = q
                    base_unit = "kos"
        log.debug(
            f"Enota ni v unit_map: u_norm={u_norm}, base_unit={base_unit}, q_norm={q_norm}"
        )

    if base_unit == "kos":
        m_weight = re.search(
            r"(?:teža|masa|weight)?\s*[:\s]?\s*(\d+(?:[.,]\d+)?)\s*(mg|g|dag|kg)\b",
            name,
            re.I,
        )
        if m_weight:
            val = Decimal(m_weight.group(1).replace(",", "."))
            unit = m_weight.group(2).lower()
            if unit == "mg":
                weight_kg = val / 1000000
            elif unit == "g":
                weight_kg = val / 1000
            elif unit == "dag":
                weight_kg = val / 100
            elif unit == "kg":
                weight_kg = val
            log.debug(
                f"Teža najdena v imenu: {val} {unit}, pretvorjeno v kg: {weight_kg}"
            )
            return q_norm * weight_kg, "kg"

        m_volume = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|l)\b", name, re.I)
        if m_volume:
            val = Decimal(m_volume.group(1).replace(",", "."))
            unit = m_volume.group(2).lower()
            if unit == "ml":
                volume_l = val / 1000
            elif unit == "l":
                volume_l = val
            log.debug(
                f"Volumen najden v imenu: {val} {unit}, pretvorjeno v L: {volume_l}"
            )
            if volume_l >= 1:
                return q_norm * volume_l, "L"
            else:
                return q_norm, "kos"

    log.debug(f"Končna normalizacija: q_norm={q_norm}, base_unit={base_unit}")
    return q_norm, base_unit


# File handling functions
def _load_supplier_map(sup_file: Path) -> dict[str, dict]:
    """Load supplier info from per-supplier JSON files or a legacy Excel."""
    log.debug(f"Branje datoteke ali mape dobaviteljev: {sup_file}")
    sup_map: dict[str, dict] = {}

    if not sup_file.exists():
        log.info(f"Mapa ali datoteka dobaviteljev {sup_file} ne obstaja")
        return sup_map

    if sup_file.is_file():
        try:
            df_sup = pd.read_excel(sup_file, dtype=str)
            log.info(f"Število prebranih dobaviteljev iz {sup_file}: {len(df_sup)}")
            for _, row in df_sup.iterrows():
                sifra = str(row["sifra"]).strip()
                ime = str(row["ime"]).strip()
                override_value = (
                    str(row.get("override_H87_to_kg", "False")).strip().lower()
                )
                override = override_value in ["true", "1", "yes"]
                sup_map[sifra] = {
                    "ime": ime or sifra,
                    "override_H87_to_kg": override,
                }
                log.debug(
                    f"Dodan v sup_map: sifra={sifra}, ime={ime}, override_value={override_value}, override={override}"
                )
            return sup_map
        except Exception as e:
            log.error(f"Napaka pri branju suppliers.xlsx: {e}")
            return {}

    links_dir = sup_file if sup_file.is_dir() else sup_file.parent
    for folder in links_dir.iterdir():
        if not folder.is_dir():
            continue
        info_path = folder / "supplier.json"
        if info_path.exists():
            try:
                data = json.loads(info_path.read_text())
                sifra = str(data.get("sifra", "")).strip()
                ime = str(data.get("ime", "")).strip() or folder.name
                raw_override = data.get("override_H87_to_kg", False)
                if isinstance(raw_override, str):
                    override = raw_override.strip().lower() in ["true", "1", "yes"]
                else:
                    override = bool(raw_override)
                if sifra:
                    sup_map[sifra] = {
                        "ime": ime,
                        "override_H87_to_kg": override,
                    }
                    log.debug(
                        f"Dodan iz JSON: sifra={sifra}, ime={ime}, override={override}"
                    )
                    # uspešno prebrali podatke, nadaljuj z naslednjo mapo
                    continue
            except Exception as e:
                log.error(f"Napaka pri branju {info_path}: {e}")
        # fallback when supplier.json is missing or neveljaven
        for file in folder.glob("*_povezane.xlsx"):
            code = file.stem.split("_")[0]
            if not code:
                continue
            if code not in sup_map:
                sup_map[code] = {
                    "ime": folder.name,
                    "override_H87_to_kg": False,
                }
                log.debug(
                    f"Dodan iz mape: sifra={code}, ime={folder.name}, override=False"
                )
            break

    log.info(f"Najdeni dobavitelji: {list(sup_map.keys())}")
    return sup_map


def _write_supplier_map(sup_map: dict, sup_file: Path):
    """Write supplier info to JSON files or legacy Excel."""
    log.debug(f"Pisanje podatkov dobaviteljev v {sup_file}")
    if sup_file.suffix == ".xlsx" or sup_file.is_file():
        sup_file.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "sifra": k,
                    "ime": v["ime"],
                    "override_H87_to_kg": v["override_H87_to_kg"],
                }
                for k, v in sup_map.items()
            ]
        )
        df.to_excel(sup_file, index=False)
        log.info(f"Datoteka uspešno zapisana: {sup_file}")
        return

    links_dir = sup_file if sup_file.is_dir() else sup_file.parent
    for code, info in sup_map.items():
        from wsm.utils import sanitize_folder_name

        folder = links_dir / sanitize_folder_name(info["ime"])
        folder.mkdir(parents=True, exist_ok=True)
        info_path = folder / "supplier.json"
        try:
            info_path.write_text(
                json.dumps(
                    {
                        "sifra": code,
                        "ime": info["ime"],
                        "override_H87_to_kg": info["override_H87_to_kg"],
                    },
                    ensure_ascii=False,
                )
            )
            log.debug(f"Zapisano {info_path}")
        except Exception as exc:
            log.error(f"Napaka pri zapisu {info_path}: {exc}")


# Save and close function
def _save_and_close(
    df,
    manual_old,
    wsm_df,
    links_file,
    root,
    supplier_name,
    supplier_code,
    sup_map,
    sup_file,
    *,
    override_h87_to_kg: bool = False,
    invoice_path=None,
    unit_file: Path | None = None,
    remember: bool = False,
    unit_value: str = "",
):
    log.debug(
        f"Shranjevanje: supplier_name={supplier_name}, supplier_code={supplier_code}"
    )
    log.info(f"Shranjujem {len(df)} vrstic z enotami: {df['enota_norm'].value_counts().to_dict()}")
    if unit_value:
        log.info(f"Enota izbirnika: {unit_value}")

    # Preverimo prazne sifra_dobavitelja
    empty_sifra = df["sifra_dobavitelja"].isna() | (df["sifra_dobavitelja"] == "")
    if empty_sifra.any():
        log.warning(
            f"Prazne vrednosti v sifra_dobavitelja za {empty_sifra.sum()} vrstic"
        )
        log.debug(
            f"Primer vrstic s prazno sifra_dobavitelja: {df[empty_sifra][['naziv', 'sifra_dobavitelja']].head().to_dict()}"
        )
        df.loc[empty_sifra, "sifra_dobavitelja"] = df.loc[empty_sifra, "naziv"].apply(
            lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8]
        )
        log.info(f"Generirane začasne šifre za {empty_sifra.sum()} vrstic")

    # Posodobi zemljevid dobaviteljev, če se je ime ali nastavitev spremenila
    old_info = sup_map.get(supplier_code, {})
    if supplier_name:
        changed = (
            old_info.get("ime") != supplier_name
            or old_info.get("override_H87_to_kg", False) != override_h87_to_kg
        )
        if changed:
            sup_map[supplier_code] = {
                "ime": supplier_name,
                "override_H87_to_kg": override_h87_to_kg,
            }
            _write_supplier_map(sup_map, sup_file)

    # Nastavi indeks za manual_old
    if not manual_old.empty:
        # Odstrani prazne ali neveljavne vrstice
        manual_old = manual_old.dropna(subset=["sifra_dobavitelja", "naziv"], how="all")
        manual_new = manual_old.set_index(["sifra_dobavitelja"])
        if "enota_norm" not in manual_new.columns:
            manual_new["enota_norm"] = pd.NA
        log.info(f"Število prebranih povezav iz manual_old: {len(manual_old)}")
        log.debug(f"Primer povezav iz manual_old: {manual_old.head().to_dict()}")
    else:
        manual_new = pd.DataFrame(
            columns=[
                "sifra_dobavitelja",
                "naziv",
                "wsm_sifra",
                "dobavitelj",
                "enota_norm",
            ]
        ).set_index(["sifra_dobavitelja"])
        log.info("Manual_old je prazen, ustvarjam nov DataFrame")

    # Ustvari df_links z istim indeksom
    df_links = df.set_index(["sifra_dobavitelja"])[
        ["naziv", "wsm_sifra", "dobavitelj", "enota_norm"]
    ]

    # Posodobi obstoječe elemente (dovoli tudi brisanje povezav)
    if manual_new.empty:
        # Če ni obstoječih povezav, začni z df_links
        manual_new = df_links.copy()
        log.debug(
            "Starting new mapping DataFrame with units: %s",
            manual_new["enota_norm"].value_counts().to_dict(),
        )
    else:
        manual_new.loc[
            df_links.index, ["naziv", "wsm_sifra", "dobavitelj", "enota_norm"]
        ] = df_links
        log.debug(
            "Updated existing mappings with new units: %s",
            manual_new["enota_norm"].value_counts().to_dict(),
        )

    # Dodaj nove elemente, ki niso v manual_new
    new_items = df_links[~df_links.index.isin(manual_new.index)]
    manual_new = pd.concat([manual_new, new_items])

    # Ponastavi indeks, da vrneš stolpce
    manual_new = manual_new.reset_index()

    # Shrani v Excel
    log.info(f"Shranjujem {len(manual_new)} povezav v {links_file}")
    log.debug(f"Primer shranjenih povezav: {manual_new.head().to_dict()}")
    if "enota_norm" in manual_new.columns:
        log.debug(
            "Units written to file: %s", manual_new["enota_norm"].value_counts().to_dict()
        )
    try:
        manual_new.to_excel(links_file, index=False)
        log.info(f"Uspešno shranjeno v {links_file}")
    except Exception as e:
        log.error(f"Napaka pri shranjevanju v {links_file}: {e}")

    invoice_hash = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import extract_service_date

            service_date = extract_service_date(invoice_path)
        except Exception as exc:
            log.warning(f"Napaka pri branju datuma storitve: {exc}")
            service_date = None
        try:
            invoice_hash = hashlib.md5(invoice_path.read_bytes()).hexdigest()
        except Exception as exc:
            log.warning(f"Napaka pri izračunu hash: {exc}")
    else:
        service_date = None
        if invoice_path and invoice_path.exists():
            try:
                invoice_hash = hashlib.md5(invoice_path.read_bytes()).hexdigest()
            except Exception as exc:
                log.warning(f"Napaka pri izračunu hash: {exc}")

    try:
        from wsm.utils import log_price_history

        log_price_history(df, links_file, service_date=service_date, invoice_id=invoice_hash)
    except Exception as exc:
        log.warning(f"Napaka pri beleženju zgodovine cen: {exc}")

    if remember and unit_file:
        try:
            unit_file.parent.mkdir(parents=True, exist_ok=True)
            unit_file.write_text(unit_value)
        except Exception as exc:
            log.warning(f"Napaka pri zapisu {unit_file}: {exc}")

    root.quit()


# Main GUI function
def review_links(
    df: pd.DataFrame,
    wsm_df: pd.DataFrame,
    links_file: Path,
    invoice_total: Decimal,
    invoice_path: Path | None = None,
) -> pd.DataFrame:
    df = df.copy()
    supplier_code = links_file.stem.split("_")[0]
    suppliers_file = Path("links")
    log.debug(f"Pot do mape links: {suppliers_file}")
    sup_map = _load_supplier_map(suppliers_file)

    log.info(f"Supplier code extracted: {supplier_code}")
    supplier_info = sup_map.get(supplier_code, {})
    default_name = supplier_info.get("ime", supplier_code)
    override_h87_to_kg = supplier_info.get("override_H87_to_kg", False)

    service_date = None
    invoice_number = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import extract_service_date, extract_invoice_number

            service_date = extract_service_date(invoice_path)
            invoice_number = extract_invoice_number(invoice_path)
        except Exception as exc:
            log.warning(f"Napaka pri branju glave računa: {exc}")

    inv_name = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import get_supplier_name

            inv_name = get_supplier_name(invoice_path)
        except Exception:
            inv_name = None
    elif invoice_path and invoice_path.suffix.lower() == ".pdf":
        try:
            from wsm.parsing.pdf import get_supplier_name_from_pdf

            inv_name = get_supplier_name_from_pdf(invoice_path)
        except Exception:
            inv_name = None
    if inv_name:
        default_name = inv_name

    log.info(f"Default name retrieved: {default_name}")
    log.debug(f"Supplier info: {supplier_info}")
    log.info(f"Override H87 to kg: {override_h87_to_kg}")

    try:
        manual_old = pd.read_excel(links_file, dtype=str)
        log.info("Processing complete")
        log.info(f"Število prebranih povezav iz {links_file}: {len(manual_old)}")
        log.debug(f"Primer povezav iz {links_file}: {manual_old.head().to_dict()}")
        empty_sifra_old = manual_old["sifra_dobavitelja"].isna() | (
            manual_old["sifra_dobavitelja"] == ""
        )
        if empty_sifra_old.any():
            log.warning(
                f"Prazne vrednosti v sifra_dobavitelja v manual_old za {empty_sifra_old.sum()} vrstic"
            )
            manual_old.loc[empty_sifra_old, "sifra_dobavitelja"] = manual_old.loc[
                empty_sifra_old, "naziv"
            ].apply(lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8])
            log.info(
                f"Generirane začasne šifre za {empty_sifra_old.sum()} vrstic v manual_old"
            )
    except Exception as e:
        manual_old = pd.DataFrame(
            columns=["sifra_dobavitelja", "naziv", "wsm_sifra", "dobavitelj"]
        )
        log.debug(
            f"Manual_old ni obstajal ali napaka pri branju: {e}, ustvarjam prazen DataFrame"
        )

    existing_names = sorted(
        {
            n
            for n in manual_old.get("dobavitelj", [])
            if isinstance(n, str) and n.strip()
        }
    )
    supplier_name = default_name
    if supplier_name and supplier_name not in existing_names:
        existing_names.insert(0, supplier_name)
    supplier_name = existing_names[0] if existing_names else supplier_code
    df["dobavitelj"] = supplier_name
    log.debug(f"Supplier name nastavljen na: {supplier_name}")

    # Generate sifra_dobavitelja for empty cases before lookup
    empty_sifra = df["sifra_dobavitelja"].isna() | (df["sifra_dobavitelja"] == "")
    if empty_sifra.any():
        df.loc[empty_sifra, "sifra_dobavitelja"] = df.loc[empty_sifra, "naziv"].apply(
            lambda x: hashlib.md5(str(x).encode()).hexdigest()[:8]
        )
        log.info(f"Generirane začasne šifre za {empty_sifra.sum()} vrstic v df")

    # Create a dictionary for quick lookup
    old_map_dict = manual_old.set_index(["sifra_dobavitelja"])["wsm_sifra"].to_dict()
    old_unit_dict = {}
    if "enota_norm" in manual_old.columns:
        old_unit_dict = manual_old.set_index(["sifra_dobavitelja"])[
            "enota_norm"
        ].to_dict()

    df["wsm_sifra"] = df.apply(
        lambda r: old_map_dict.get((r["sifra_dobavitelja"]), pd.NA), axis=1
    )
    df["wsm_naziv"] = df["wsm_sifra"].map(wsm_df.set_index("wsm_sifra")["wsm_naziv"])
    df["status"] = df["wsm_sifra"].notna().map({True: "POVEZANO", False: pd.NA})
    log.debug(f"df po inicializaciji: {df.head().to_dict()}")

    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    doc_discount_total = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"]
    # Ensure a clean sequential index so Treeview item IDs are predictable
    df = df.reset_index(drop=True)
    df["cena_pred_rabatom"] = df.apply(
        lambda r: (r["vrednost"] + r["rabata"]) / r["kolicina"]
        if r["kolicina"]
        else Decimal("0"),
        axis=1,
    )
    df["cena_po_rabatu"] = df.apply(
        lambda r: r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0"),
        axis=1,
    )
    df["rabata_pct"] = df.apply(
        lambda r: (
            ((r["rabata"] / (r["vrednost"] + r["rabata"])) * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if (r["vrednost"] + r["rabata"])
            else Decimal("0.00")
        ),
        axis=1,
    )
    df["total_net"] = df["vrednost"]
    df["kolicina_norm"], df["enota_norm"] = zip(
        *[
            _norm_unit(Decimal(str(q)), u, n, override_h87_to_kg)
            for q, u, n in zip(df["kolicina"], df["enota"], df["naziv"])
        ]
    )
    if old_unit_dict:
        log.debug(f"Old unit mapping loaded: {old_unit_dict}")
        def _restore_unit(r):
            if override_h87_to_kg and str(r["enota"]).upper() == "H87":
                return r["enota_norm"]
            return old_unit_dict.get(r["sifra_dobavitelja"], r["enota_norm"])

        before = df["enota_norm"].copy()
        df["enota_norm"] = df.apply(_restore_unit, axis=1)
        changed = (before != df["enota_norm"]).sum()
        log.debug(f"Units restored from old map: {changed} rows updated")
        log.debug(
            "Units after applying saved mapping: %s",
            df["enota_norm"].value_counts().to_dict(),
        )
    df["kolicina_norm"] = df["kolicina_norm"].astype(float)
    log.debug(f"df po normalizaciji: {df.head().to_dict()}")

    # If totals differ slightly (<=5 cent), adjust the document discount when
    # its line exists. Otherwise record the difference separately so that totals
    # still match the invoice without showing an extra row.
    calculated_total = df["total_net"].sum() + doc_discount_total
    diff = invoice_total - calculated_total
    step = detect_round_step(invoice_total, calculated_total)
    if abs(diff) <= step and diff != 0:
        if not df_doc.empty:
            log.debug(
                f"Prilagajam dokumentarni popust za razliko {diff}: "
                f"{doc_discount_total} -> {doc_discount_total + diff}"
            )
            doc_discount_total += diff
            df_doc.loc[df_doc.index, "vrednost"] += diff
            df_doc.loc[df_doc.index, "cena_bruto"] += abs(diff)
            df_doc.loc[df_doc.index, "rabata"] += abs(diff)
        else:
            log.debug(

                f"Dodajam _DOC_ vrstico za razliko {diff} med vrsticami in računom"
            )
            df_doc = pd.DataFrame(
                [
                    {
                        "sifra_dobavitelja": "_DOC_",
                        "naziv": "Samodejni popravek",
                        "kolicina": Decimal("1"),
                        "enota": "",
                        "cena_bruto": abs(diff),
                        "cena_netto": Decimal("0"),
                        "rabata": abs(diff),
                        "rabata_pct": Decimal("100.00"),
                        "vrednost": diff,
                    }
                ]

            )
            doc_discount_total += diff

    root = tk.Tk()
    root.title(f"Ročna revizija – {supplier_name}")
    header = f"Dobavitelj: {supplier_name}"
    if service_date:
        header += f" | Datum storitve: {service_date}"
    if invoice_number:
        header += f" | Račun: {invoice_number}"
    tk.Label(root, text=header, font=("Arial", 14, "bold")).pack(pady=4)
    # Start in fullscreen; press Esc to exit
    root.attributes("-fullscreen", True)
    root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
    cols = [
        "naziv",
        "kolicina_norm",
        "enota_norm",
        "rabata_pct",
        "cena_pred_rabatom",
        "cena_po_rabatu",
        "total_net",
        "wsm_naziv",
        "dobavitelj",
    ]
    heads = [
        "Naziv artikla",
        "Količina",
        "Enota",
        "Rabat (%)",
        "Net. pred rab.",
        "Net. po rab.",
        "Skupna neto",
        "WSM naziv",
        "Dobavitelj",
    ]
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=27)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    for c, h in zip(cols, heads):
        tree.heading(c, text=h)
        width = 300 if c == "naziv" else (80 if c == "enota_norm" else 120)
        tree.column(c, width=width, anchor="w")
    for i, row in df.iterrows():
        vals = [
            (
                _fmt(row[c])
                if isinstance(row[c], (Decimal, float, int))
                else ("" if pd.isna(row[c]) else str(row[c]))
            )
            for c in cols
        ]
        tree.insert("", "end", iid=str(i), values=vals)
    tree.focus("0")
    tree.selection_set("0")

    # Povzetek skupnih neto cen po WSM šifrah
    summary_frame = tk.Frame(root)
    summary_frame.pack(fill="both", expand=True, pady=10)
    tk.Label(
        summary_frame, text="Povzetek po WSM šifrah", font=("Arial", 12, "bold")
    ).pack()

    summary_cols = [
        "wsm_sifra",
        "wsm_naziv",
        "kolicina_norm",
        "neto_brez_popusta",
        "rabata_pct",
        "vrednost",
    ]
    summary_heads = [
        "WSM Šifra",
        "WSM Naziv",
        "Količina",
        "Znesek",
        "Rabat (%)",
        "Neto po rabatu",
    ]
    summary_tree = ttk.Treeview(
        summary_frame, columns=summary_cols, show="headings", height=5
    )
    vsb_summary = ttk.Scrollbar(
        summary_frame, orient="vertical", command=summary_tree.yview
    )
    summary_tree.configure(yscrollcommand=vsb_summary.set)
    vsb_summary.pack(side="right", fill="y")
    summary_tree.pack(side="left", fill="both", expand=True)

    for c, h in zip(summary_cols, summary_heads):
        summary_tree.heading(c, text=h)
        summary_tree.column(c, width=150, anchor="w")

    def _update_summary():
        for item in summary_tree.get_children():
            summary_tree.delete(item)
        required = {"wsm_sifra", "vrednost", "rabata", "kolicina_norm", "rabata_pct"}
        if required.issubset(df.columns):
            summary_df = (
                df[df["wsm_sifra"].notna()]
                .groupby(["wsm_sifra", "rabata_pct"], dropna=False)
                .agg(
                    {
                        "vrednost": "sum",
                        "rabata": "sum",
                        "kolicina_norm": "sum",
                    }
                )
                .reset_index()
            )

            summary_df["neto_brez_popusta"] = summary_df["vrednost"] + summary_df["rabata"]
            summary_df["wsm_naziv"] = summary_df["wsm_sifra"].map(
                wsm_df.set_index("wsm_sifra")["wsm_naziv"]
            )
            summary_df["rabata_pct"] = [
                (
                    (row["rabata"] / row["neto_brez_popusta"] * Decimal("100")).quantize(
                        Decimal("0.01")
                    )
                    if row["neto_brez_popusta"]
                    else Decimal("0.00")
                )
                for _, row in summary_df.iterrows()
            ]

            for _, row in summary_df.iterrows():
                vals = [
                    row["wsm_sifra"],
                    row["wsm_naziv"],
                    _fmt(row["kolicina_norm"]),
                    _fmt(row["neto_brez_popusta"]),
                    _fmt(row["rabata_pct"]),
                    _fmt(row["vrednost"]),
                ]
                summary_tree.insert("", "end", values=vals)
            log.debug(f"Povzetek posodobljen: {len(summary_df)} WSM šifer")

    # Skupni zneski pod povzetkom
    total_frame = tk.Frame(root)
    total_frame.pack(fill="x", pady=5)

    # Dokumentarni popust obravnavamo kot povezan znesek, saj ne potrebuje
    # dodatne ročne obdelave. Zato ga prištejemo k "Skupaj povezano" in ga
    # ne štejemo med "Skupaj ostalo".
    if df["wsm_sifra"].notna().any():
        # Ko je vsaj ena vrstica povezana, dokumentarni popust štejemo
        # kot "povezan" znesek, saj ga uporabnik ne obravnava ročno.
        linked_total = (
            df[df["wsm_sifra"].notna()]["total_net"].sum() + doc_discount_total
        )
        unlinked_total = df[df["wsm_sifra"].isna()]["total_net"].sum()
    else:
        # Če ni še nobene povezave, popust prištejemo k "ostalim" vrsticam,
        # da "Skupaj povezano" ostane ničelno.
        linked_total = df[df["wsm_sifra"].notna()]["total_net"].sum()
        unlinked_total = (
            df[df["wsm_sifra"].isna()]["total_net"].sum() + doc_discount_total
        )
    # Skupni seštevek mora biti vsota "povezano" in "ostalo"
    total_sum = linked_total + unlinked_total
    step_total = detect_round_step(invoice_total, total_sum)
    match_symbol = "✓" if abs(total_sum - invoice_total) <= step_total else "✗"

    tk.Label(
        total_frame,
        text=f"Skupaj povezano: {_fmt(linked_total)} € + Skupaj ostalo: {_fmt(unlinked_total)} € = Skupni seštevek: {_fmt(total_sum)} € | Skupna vrednost računa: {_fmt(invoice_total)} € {match_symbol}",
        font=("Arial", 10, "bold"),
        name="total_sum",
    ).pack(side="left", padx=10)

    def _update_totals():
        if df["wsm_sifra"].notna().any():
            linked_total = (
                df[df["wsm_sifra"].notna()]["total_net"].sum() + doc_discount_total
            )
            unlinked_total = df[df["wsm_sifra"].isna()]["total_net"].sum()
        else:
            linked_total = df[df["wsm_sifra"].notna()]["total_net"].sum()
            unlinked_total = (
                df[df["wsm_sifra"].isna()]["total_net"].sum() + doc_discount_total
            )
        total_sum = linked_total + unlinked_total
        step_total = detect_round_step(invoice_total, total_sum)
        match_symbol = "✓" if abs(total_sum - invoice_total) <= step_total else "✗"
        total_frame.children["total_sum"].config(
            text=f"Skupaj povezano: {_fmt(linked_total)} € + Skupaj ostalo: {_fmt(unlinked_total)} € = Skupni seštevek: {_fmt(total_sum)} € | Skupna vrednost računa: {_fmt(invoice_total)} € {match_symbol}"
        )

    bottom = tk.Frame(root)
    bottom.pack(fill="x", padx=8, pady=6)
    custom = tk.Frame(bottom)
    custom.pack(fill="x")
    tk.Label(custom, text="Vpiši / izberi WSM naziv:").pack(side="left")
    entry = tk.Entry(custom)
    entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
    lb = tk.Listbox(custom, height=6)

    # --- Unit change widgets ---
    unit_options = ["kos", "kg", "L"]
    last_unit_file = Path("links") / "last_unit.txt"

    unit_from_xml = df["enota_norm"].mode().iat[0] if not df.empty else "kg"
    remember_default = False
    _last_unit = unit_from_xml
    if last_unit_file.exists():
        remember_default = True
        try:
            val = last_unit_file.read_text().strip()
            if val:
                _last_unit = val
        except Exception as exc:
            log.debug(f"Napaka pri branju {last_unit_file}: {exc}")

    unit_var = tk.StringVar(
        value=_last_unit if _last_unit in unit_options else unit_options[0]
    )
    unit_menu = ttk.Combobox(
        bottom, values=unit_options, textvariable=unit_var, state="readonly", width=5
    )

    def _on_unit_select(event=None):
        val = unit_var.get()
        log.info(f"Combobox selected: {val}")
        log.debug(
            "Units before any override: %s",
            df["enota_norm"].value_counts().to_dict(),
        )

    unit_menu.bind("<<ComboboxSelected>>", _on_unit_select)
    unit_var.trace_add(
        "write", lambda *_: log.info(f"unit_var changed: {unit_var.get()}")
    )

    def _set_all_units():
        new_u = unit_var.get()
        before = df["enota_norm"].copy()
        log.info(f"Nastavljam vse enote na {new_u}")
        log.debug(
            "Units distribution pre-override: %s",
            before.value_counts().to_dict(),
        )
        df["enota_norm"] = new_u
        df["enota"] = new_u
        for item in tree.get_children():
            tree.set(item, "enota_norm", new_u)
        changed = (before != df["enota_norm"]).sum()
        if changed:
            log.info(f"Spremenjenih vrstic: {changed}")
        else:
            log.warning("Nobena vrstica ni bila spremenjena pri nastavitvi enote")
        log.info(
            "Units after override: %s",
            df["enota_norm"].value_counts().to_dict(),
        )
        root.update()  # refresh UI so the combobox selection is respected
        log.debug(
            "Units after root.update: %s (combobox=%s)",
            df["enota_norm"].value_counts().to_dict(),
            unit_var.get(),
        )
        _update_summary()
        _update_totals()

    remember_var = tk.BooleanVar(value=remember_default)

    save_btn = tk.Button(
        bottom,
        text="Shrani & zapri",
        width=14,
        command=lambda e=None: _save_and_close(
            df,
            manual_old,
            wsm_df,
            links_file,
            root,
            supplier_name,
            supplier_code,
            sup_map,
            suppliers_file,
            override_h87_to_kg=override_h87_to_kg,
            invoice_path=invoice_path,
            unit_file=last_unit_file,
            remember=remember_var.get(),
            unit_value=unit_var.get(),
        ),
    )

    def _exit():
        if remember_var.get():
            try:
                last_unit_file.parent.mkdir(parents=True, exist_ok=True)
                last_unit_file.write_text(unit_var.get())
            except Exception as exc:
                log.warning(f"Napaka pri zapisu {last_unit_file}: {exc}")
        root.quit()

    exit_btn = tk.Button(
        bottom,
        text="Izhod",
        width=14,
        command=_exit,
    )
    exit_btn.pack(side="right", padx=(6, 0))
    save_btn.pack(side="right", padx=(6, 0))
    tk.Checkbutton(
        bottom,
        text="Zapomni enoto",
        variable=remember_var,
        onvalue=True,
        offvalue=False,
    ).pack(side="right", padx=(0, 20))
    unit_menu.pack(side="right", padx=(6, 0))
    tk.Button(bottom, text="Nastavi vse enote", command=_set_all_units).pack(
        side="right", padx=(0, 20)
    )

    root.bind(
        "<F10>",
        lambda e: _save_and_close(
            df,
            manual_old,
            wsm_df,
            links_file,
            root,
            supplier_name,
            supplier_code,
            sup_map,
            suppliers_file,
            override_h87_to_kg=override_h87_to_kg,
            invoice_path=invoice_path,
            unit_file=last_unit_file,
            remember=remember_var.get(),
            unit_value=unit_var.get(),
        ),
    )

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
        if evt and evt.keysym in {
            "Return",
            "Escape",
            "Up",
            "Down",
            "Tab",
            "Right",
            "Left",
        }:
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
        nxt = max(0, min(lb.size() - 1, nxt))
        lb.selection_clear(0, "end")
        lb.selection_set(nxt)
        lb.activate(nxt)
        lb.see(nxt)
        return "break"

    def _edit_unit(evt):
        col = tree.identify_column(evt.x)
        row_id = tree.identify_row(evt.y)
        if col != "#3" or not row_id:
            return
        idx = int(row_id)
        top = tk.Toplevel(root)
        top.title("Spremeni enoto")
        var = tk.StringVar(value=df.at[idx, "enota_norm"])
        cb = ttk.Combobox(top, values=unit_options, textvariable=var, state="readonly")
        cb.pack(padx=10, pady=10)

        def _apply(_=None):
            new_u = var.get()
            df.at[idx, "enota_norm"] = new_u
            tree.set(row_id, "enota_norm", new_u)
            _update_summary()
            _update_totals()
            top.destroy()

        tk.Button(top, text="OK", command=_apply).pack(pady=(0, 10))
        cb.bind("<Return>", _apply)
        cb.focus_set()
        return "break"

    def _confirm(_=None):
        sel_i = tree.focus()
        if not sel_i:
            return "break"
        choice = (
            lb.get(lb.curselection()[0]) if lb.curselection() else entry.get().strip()
        )
        idx = int(sel_i)
        df.at[idx, "wsm_naziv"] = choice
        df.at[idx, "wsm_sifra"] = n2s.get(choice, pd.NA)
        df.at[idx, "status"] = "POVEZANO"
        df.at[idx, "dobavitelj"] = supplier_name
        if (
            pd.isna(df.at[idx, "sifra_dobavitelja"])
            or df.at[idx, "sifra_dobavitelja"] == ""
        ):
            df.at[idx, "sifra_dobavitelja"] = hashlib.md5(
                str(df.at[idx, "naziv"]).encode()
            ).hexdigest()[:8]
        new_vals = [
            (
                _fmt(df.at[idx, c])
                if isinstance(df.at[idx, c], (Decimal, float, int))
                else ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c]))
            )
            for c in cols
        ]
        tree.item(sel_i, values=new_vals)
        log.debug(
            f"Potrjeno: idx={idx}, wsm_naziv={choice}, wsm_sifra={df.at[idx, 'wsm_sifra']}, sifra_dobavitelja={df.at[idx, 'sifra_dobavitelja']}"
        )
        _update_summary()  # Update summary after confirming
        _update_totals()  # Update totals after confirming
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
        new_vals = [
            (
                _fmt(df.at[idx, c])
                if isinstance(df.at[idx, c], (Decimal, float, int))
                else ("" if pd.isna(df.at[idx, c]) else str(df.at[idx, c]))
            )
            for c in cols
        ]
        tree.item(sel_i, values=new_vals)
        log.debug(f"Povezava odstranjena: idx={idx}, wsm_naziv=NaN, wsm_sifra=NaN")
        _update_summary()  # Update summary after clearing
        _update_totals()  # Update totals after clearing
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
    tree.bind("<Double-Button-1>", _edit_unit)

    # Vezave za entry in lb
    entry.bind("<KeyRelease>", _suggest)
    entry.bind("<Down>", _init_listbox)
    entry.bind("<Tab>", _init_listbox)
    entry.bind("<Right>", _init_listbox)
    entry.bind("<Return>", _confirm)
    entry.bind(
        "<Escape>",
        lambda e: (lb.pack_forget(), entry.delete(0, "end"), tree.focus_set(), "break"),
    )
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
