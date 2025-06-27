from __future__ import annotations

from pathlib import Path
import json
import logging
import shutil
import pandas as pd

from .utils import sanitize_folder_name

log = logging.getLogger(__name__)


def load_suppliers(sup_file: Path) -> dict[str, dict]:
    """Load supplier info from per-supplier JSON files or a legacy Excel."""
    log.debug("Branje datoteke ali mape dobaviteljev: %s", sup_file)
    sup_map: dict[str, dict] = {}

    if not sup_file.exists():
        log.info("Mapa ali datoteka dobaviteljev %s ne obstaja", sup_file)
        return sup_map

    if sup_file.is_file():
        try:
            df_sup = pd.read_excel(sup_file, dtype=str)
            log.info("\u0160tevilo prebranih dobaviteljev iz %s: %s", sup_file, len(df_sup))
            for _, row in df_sup.iterrows():
                sifra = str(row["sifra"]).strip()
                ime = str(row["ime"]).strip()
                vat = str(row.get("vat") or row.get("davcna") or "").strip()
                sup_map[sifra] = {"ime": ime or sifra, "vat": vat}
                log.debug("Dodan v sup_map: sifra=%s, ime=%s", sifra, ime)
            return sup_map
        except Exception as e:
            log.error("Napaka pri branju suppliers.xlsx: %s", e)
            return {}

    links_dir = sup_file if sup_file.is_dir() else sup_file.parent
    log.info("Pregledujem mapo dobaviteljev: %s", links_dir)
    for folder in links_dir.iterdir():
        if not folder.is_dir():
            continue
        info_path = folder / "supplier.json"
        data = {}
        if info_path.exists():
            try:
                data = json.loads(info_path.read_text())
            except Exception as e:
                log.error("Napaka pri branju %s: %s", info_path, e)
        sifra = str(data.get("sifra", "")).strip()
        ime = str(data.get("ime", "")).strip() or folder.name
        vat = str(data.get("vat") or data.get("davcna") or "").strip()
        if vat:
            safe_vat = sanitize_folder_name(vat)
            if safe_vat != folder.name:
                new_folder = links_dir / safe_vat
                try:
                    if not new_folder.exists():
                        try:
                            shutil.move(str(folder), str(new_folder))
                        except Exception as move_exc:
                            log.debug("Fallback to per-file move: %s", move_exc)
                            new_folder.mkdir(parents=True, exist_ok=True)
                            for p in folder.iterdir():
                                target = new_folder / p.name
                                if not target.exists():
                                    p.rename(target)
                            try:
                                folder.rmdir()
                            except OSError:
                                pass
                    else:
                        for p in folder.iterdir():
                            target = new_folder / p.name
                            if not target.exists():
                                p.rename(target)
                        try:
                            folder.rmdir()
                        except OSError:
                            pass
                    folder = new_folder
                    info_path = folder / "supplier.json"
                except Exception as exc:
                    log.warning("Napaka pri preimenovanju %s v %s: %s", folder, new_folder, exc)
        if sifra:
            sup_map[sifra] = {"ime": ime, "vat": vat}
            log.debug("Dodan iz JSON: sifra=%s, ime=%s", sifra, ime)
            continue
        for file in folder.glob("*_povezane.xlsx"):
            code = file.stem.split("_")[0]
            if not code:
                continue
            if code not in sup_map:
                sup_map[code] = {"ime": folder.name, "vat": ""}
                log.debug("Dodan iz mape: sifra=%s, ime=%s", code, folder.name)
            break
        hist_path = folder / "price_history.xlsx"
        if hist_path.exists():
            try:
                df_hist = pd.read_excel(hist_path)
                if df_hist.empty:
                    log.debug("Prazna datoteka zgodovine cen: %s", hist_path)
                    continue
                if "code" in df_hist.columns:
                    codes = df_hist["code"].dropna().astype(str)
                    code = str(codes.iloc[0]) if not codes.empty else None
                elif "key" in df_hist.columns:
                    keys = df_hist["key"].dropna().astype(str)
                    code = str(keys.iloc[0]).split("_")[0] if not keys.empty else None
                else:
                    code = None
            except Exception as exc:
                log.error("Napaka pri branju %s: %s", hist_path, exc)
                code = None
            if code and code not in sup_map:
                sup_map[code] = {"ime": folder.name, "vat": ""}
                log.debug("Dodan iz price_history: sifra=%s, ime=%s", code, folder.name)
        if folder.name and folder.name not in {info.get("ime") for info in sup_map.values()}:
            code = sanitize_folder_name(folder.name)
            if code not in sup_map:
                sup_map[code] = {"ime": folder.name, "vat": ""}
                log.debug("Dodan iz imena mape: sifra=%s, ime=%s", code, folder.name)
    log.info("Najdeni dobavitelji: %s", list(sup_map.keys()))
    return sup_map


def save_supplier(sup_map: dict, sup_file: Path) -> None:
    """Write supplier info to JSON files or legacy Excel."""
    log.debug("Pisanje podatkov dobaviteljev v %s", sup_file)
    if sup_file.suffix == ".xlsx" or sup_file.is_file():
        sup_file.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [{"sifra": k, "ime": v["ime"], "vat": v.get("vat", "")} for k, v in sup_map.items()]
        )
        df.to_excel(sup_file, index=False)
        log.info("Datoteka uspe\u0161no zapisana: %s", sup_file)
        return

    is_dir_path = sup_file.is_dir() or sup_file.suffix == ""
    if is_dir_path:
        if not sup_file.exists():
            sup_file.mkdir(parents=True, exist_ok=True)
        links_dir = sup_file
    else:
        links_dir = sup_file.parent

    for code, info in sup_map.items():
        folder = links_dir / sanitize_folder_name(info.get("vat") or info["ime"])
        folder.mkdir(parents=True, exist_ok=True)
        info_path = folder / "supplier.json"
        try:
            info_path.write_text(
                json.dumps({"sifra": code, "ime": info["ime"], "vat": info.get("vat")}, ensure_ascii=False)
            )
            log.debug("Zapisano %s", info_path)
        except Exception as exc:
            log.error("Napaka pri zapisu %s: %s", info_path, exc)


