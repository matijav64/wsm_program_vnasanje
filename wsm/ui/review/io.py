from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

import pandas as pd
from tkinter import messagebox

from wsm.utils import _clean
from wsm.supplier_store import (
    load_suppliers as _load_supplier_map,
    save_supplier as _write_supplier_map,
)

__all__ = ["_load_supplier_map", "_save_and_close"]

log = logging.getLogger(__name__)


def _update_supplier_info(
    df: pd.DataFrame,
    links_file: Path,
    supplier_name: str,
    supplier_code: str,
    sup_map: dict,
    sup_file: Path,
    vat: str | None,
) -> tuple[Path, Path]:
    """Return updated links file path and supplier folder."""

    df["sifra_dobavitelja"] = df["sifra_dobavitelja"].fillna("").astype(str)
    empty_sifra = df["sifra_dobavitelja"] == ""
    if empty_sifra.any():
        log.warning(
            "Prazne vrednosti v sifra_dobavitelja za %s vrstic",
            empty_sifra.sum(),
        )
        sample = (
            df[empty_sifra][["naziv", "sifra_dobavitelja"]].head().to_dict()
        )
        log.debug("Primer vrstic s prazno sifra_dobavitelja: %s", sample)

    new_info = sup_map.get(supplier_code, {}).copy()
    changed = False
    if vat and new_info.get("vat") != vat:
        new_info["vat"] = vat
        changed = True
    if supplier_name and new_info.get("ime") != supplier_name:
        new_info["ime"] = supplier_name
        changed = True

    from wsm.utils import sanitize_folder_name
    from wsm.supplier_store import choose_supplier_key

    old_safe = links_file.parent.name
    new_key = choose_supplier_key(vat, supplier_code)
    if not new_key:
        messagebox.showwarning(
            "Opozorilo",
            "Davčna številka dobavitelja ni znana; mapa ne bo preimenovana.",
        )
        new_folder = links_file.parent
        new_safe = old_safe
    else:
        new_safe = sanitize_folder_name(new_key)
        if new_safe != old_safe:
            old_folder = links_file.parent
            new_folder = Path(sup_file) / new_safe
            moved = False
            try:
                if not new_folder.exists():
                    shutil.move(str(old_folder), str(new_folder))
                    moved = True
                else:
                    target = (
                        new_folder
                        / f"{supplier_code}_{new_safe}_povezane.xlsx"
                    )
                    if links_file.exists():
                        if target.exists():
                            target = target.with_stem(target.stem + "_old")
                        links_file.rename(target)
                    for p in old_folder.iterdir():
                        dest = new_folder / p.name
                        if dest.exists():
                            dest = dest.with_stem(dest.stem + "_old")
                        shutil.move(str(p), str(dest))
                    shutil.rmtree(old_folder, ignore_errors=True)
                    moved = True
            except Exception as exc:
                log.warning(
                    "Napaka pri preimenovanju %s v %s: %s",
                    old_folder,
                    new_folder,
                    exc,
                )
                try:
                    new_folder.mkdir(exist_ok=True)
                    for p in old_folder.iterdir():
                        dest = new_folder / p.name
                        if dest.exists():
                            dest = dest.with_stem(dest.stem + "_old")
                        shutil.move(str(p), str(dest))
                    shutil.rmtree(old_folder, ignore_errors=True)
                    moved = True
                except Exception as exc2:
                    log.warning(
                        "Napaka pri prenosu vsebine %s v %s: %s",
                        old_folder,
                        new_folder,
                        exc2,
                    )
            if moved:
                if supplier_code.casefold() == new_safe.casefold():
                    links_file = new_folder / f"{supplier_code}_povezane.xlsx"
                else:
                    links_file = (
                        new_folder
                        / f"{supplier_code}_{new_safe}_povezane.xlsx"
                    )
                unk_folder = Path(sup_file) / "unknown"
                if unk_folder.exists():
                    shutil.rmtree(unk_folder, ignore_errors=True)
        else:
            new_folder = Path(sup_file) / new_safe
            if supplier_code.casefold() == new_safe.casefold():
                links_file = new_folder / f"{supplier_code}_povezane.xlsx"
            else:
                links_file = (
                    new_folder / f"{supplier_code}_{new_safe}_povezane.xlsx"
                )

    for p in links_file.parent.glob(
        f"{supplier_code}_{supplier_code}_povezane.xlsx"
    ):
        try:
            if p != links_file:
                p.unlink()
        except Exception:
            pass

    if "unknown" in sup_map and supplier_code != "unknown":
        sup_map.pop("unknown", None)
        changed = True
        unk_folder = Path(sup_file) / "unknown"
        if unk_folder.exists():
            shutil.rmtree(unk_folder, ignore_errors=True)

    if changed or supplier_code not in sup_map:
        sup_map[supplier_code] = new_info
        _write_supplier_map(sup_map, sup_file)

    return links_file, new_folder


def _write_excel_links(
    df: pd.DataFrame,
    manual_old: pd.DataFrame,
    links_file: Path,
) -> None:
    """Write updated mappings to ``links_file``."""

    if not manual_old.empty:
        manual_old = manual_old.dropna(
            subset=["sifra_dobavitelja", "naziv"],
            how="all",
        )
        manual_old["naziv_ckey"] = manual_old["naziv"].map(_clean)
        manual_new = manual_old.set_index(["sifra_dobavitelja", "naziv_ckey"])
        if "enota_norm" not in manual_new.columns:
            manual_new["enota_norm"] = pd.NA
        log.info(
            "Število prebranih povezav iz manual_old: %s",
            len(manual_old),
        )
        log.debug(
            "Primer povezav iz manual_old: %s",
            manual_old.head().to_dict(),
        )
    else:
        manual_new = pd.DataFrame(
            columns=[
                "sifra_dobavitelja",
                "naziv",
                "naziv_ckey",
                "wsm_sifra",
                "dobavitelj",
                "enota_norm",
            ]
        ).set_index(["sifra_dobavitelja", "naziv_ckey"])
        log.info("Manual_old je prazen, ustvarjam nov DataFrame")

    df["sifra_dobavitelja"] = df["sifra_dobavitelja"].fillna("").astype(str)
    df["naziv_ckey"] = df["naziv"].map(_clean)
    df_links = df.set_index(["sifra_dobavitelja", "naziv_ckey"])[
        ["naziv", "wsm_sifra", "dobavitelj", "enota_norm"]
    ]

    if manual_new.empty:
        manual_new = df_links.copy()
        log.debug(
            "Starting new mapping DataFrame with units: %s",
            manual_new["enota_norm"].value_counts().to_dict(),
        )
    else:
        common = manual_new.index.intersection(df_links.index)
        if not common.empty:
            manual_new.loc[
                common,
                ["naziv", "wsm_sifra", "dobavitelj", "enota_norm"],
            ] = df_links.loc[common]
            log.debug(
                "Updated existing mappings with new units: %s",
                manual_new["enota_norm"].value_counts().to_dict(),
            )

    new_items = df_links[~df_links.index.isin(manual_new.index)]
    manual_new = pd.concat([manual_new, new_items])
    manual_new = manual_new.reset_index()

    log.info(f"Shranjujem {len(manual_new)} povezav v {links_file}")
    log.debug(f"Primer shranjenih povezav: {manual_new.head().to_dict()}")
    if "enota_norm" in manual_new.columns:
        log.debug(
            "Units written to file: %s",
            manual_new["enota_norm"].value_counts().to_dict(),
        )
    try:
        manual_new.to_excel(links_file, index=False)
        log.info(f"Uspešno shranjeno v {links_file}")
    except Exception as e:
        log.error(f"Napaka pri shranjevanju v {links_file}: {e}")


def _write_history_files(
    df: pd.DataFrame,
    invoice_path: Path | None,
    new_folder: Path,
    links_file: Path,
    sup_file: Path,
    root,
) -> bool:
    """Record price history and clean temporary files."""

    invoice_hash = None
    if invoice_path and invoice_path.suffix.lower() == ".xml":
        try:
            from wsm.parsing.eslog import extract_service_date

            service_date = extract_service_date(invoice_path)
        except Exception as exc:
            log.warning("Napaka pri branju datuma storitve: %s", exc)
            service_date = None
        try:
            invoice_hash = hashlib.md5(invoice_path.read_bytes()).hexdigest()
        except Exception as exc:
            log.warning("Napaka pri izračunu hash: %s", exc)
    elif invoice_path and invoice_path.suffix.lower() == ".pdf":
        try:
            from wsm.parsing.pdf import extract_service_date

            service_date = extract_service_date(invoice_path)
        except Exception as exc:
            log.warning("Napaka pri branju datuma storitve: %s", exc)
            service_date = None
        try:
            invoice_hash = hashlib.md5(invoice_path.read_bytes()).hexdigest()
        except Exception as exc:
            log.warning("Napaka pri izračunu hash: %s", exc)
    else:
        service_date = None
        if invoice_path and invoice_path.exists():
            try:
                invoice_hash = hashlib.md5(
                    invoice_path.read_bytes()
                ).hexdigest()
            except Exception as exc:
                log.warning("Napaka pri izračunu hash: %s", exc)

    if invoice_hash:
        from wsm.utils import history_contains

        history_file = new_folder / "price_history.xlsx"
        try:
            exists = history_contains(invoice_hash, history_file)
        except Exception as exc:
            log.warning(f"Napaka pri preverjanju podvojenega računa: {exc}")
            exists = False
        if exists:
            proceed = messagebox.askyesno(
                "Opozorilo",
                "Račun je že zabeležen v price_history.xlsx. Shranim vseeno?",
            )
            if not proceed:
                unk = Path(sup_file) / "unknown"
                if unk.exists():
                    try:
                        shutil.rmtree(unk, ignore_errors=True)
                    except Exception:
                        pass
                root.quit()
                return True

    try:
        from wsm.utils import log_price_history

        log_price_history(
            df,
            links_file,
            service_date=service_date,
            suppliers_dir=sup_file,
            invoice_id=invoice_hash,
        )
    except Exception as exc:
        log.warning(f"Napaka pri beleženju zgodovine cen: {exc}")

    unk = Path(sup_file) / "unknown"
    if unk.exists():
        try:
            shutil.rmtree(unk, ignore_errors=True)
        except Exception:
            pass

    return False


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
    invoice_path=None,
    vat=None,
):
    """Save mapping files, record history and close the window.

    Args:
        df (pandas.DataFrame): Invoice rows to persist.
        manual_old (pandas.DataFrame): Previously saved mappings from
            ``links_file``.
        wsm_df (pandas.DataFrame): Table of WSM articles. Currently unused.
        links_file (pathlib.Path): Excel file where mappings are written.
        root: ``tkinter`` root object that will be terminated.
        supplier_name (str): Display name of the supplier.
        supplier_code (str): Identifier used as a key in ``sup_map``.
        sup_map (dict): Supplier metadata loaded from ``sup_file``.
        sup_file (pathlib.Path): Directory containing per-supplier data.
        invoice_path (pathlib.Path | None, optional): Path to the processed
            invoice for history logging.
        vat (str | None, optional): Supplier VAT number.

    Returns:
        None

    Raises:
        Exception: Propagated if updating supplier info or writing files fails.
    """
    log.debug(
        "Shranjevanje: supplier_name=%s, supplier_code=%s",
        supplier_name,
        supplier_code,
    )

    log.info(
        "Shranjujem %s vrstic z enotami: %s",
        len(df),
        df["enota_norm"].value_counts().to_dict(),
    )

    links_file, new_folder = _update_supplier_info(
        df, links_file, supplier_name, supplier_code, sup_map, sup_file, vat
    )

    _write_excel_links(df, manual_old, links_file)

    should_stop = _write_history_files(
        df,
        invoice_path,
        new_folder,
        links_file,
        sup_file,
        root,
    )

    if not should_stop:
        root.quit()
