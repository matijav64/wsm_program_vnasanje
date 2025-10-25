from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple, Dict
import pandas as pd

from wsm.utils import sanitize_folder_name


@dataclass
class Supplier:
    """Data structure for supplier information and related tables."""

    code: str
    name: str
    vat: str | None = None
    links: pd.DataFrame = field(default_factory=pd.DataFrame)
    history: pd.DataFrame = field(default_factory=pd.DataFrame)


def _safe_read_sheet(xl: pd.ExcelFile, sheet: str) -> pd.DataFrame:
    """Return sheet as DataFrame if present, else empty DataFrame."""
    if sheet in xl.sheet_names:
        try:
            return xl.parse(sheet)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def load_suppliers(root: Path) -> Tuple[Dict[str, Supplier], Dict[str, Supplier]]:
    """Load suppliers from ``root``.

    Returns a tuple ``(suppliers, name_index)`` where ``suppliers`` is a mapping
    of supplier code to :class:`Supplier` and ``name_index`` maps folder names to
    ``Supplier``.
    """
    suppliers: Dict[str, Supplier] = {}
    name_index: Dict[str, Supplier] = {}
    if not root.exists():
        return suppliers, name_index

    for folder in root.iterdir():
        if folder.is_dir():
            xlsx_path = folder / "supplier.xlsx"
            folder_key = folder.name
        elif folder.suffix.lower() == ".xlsx":
            xlsx_path = folder
            folder_key = folder.stem
        else:
            continue
        if not xlsx_path.exists():
            continue
        try:
            xl = pd.ExcelFile(xlsx_path)
        except Exception:
            continue
        info = _safe_read_sheet(xl, "info")
        links = _safe_read_sheet(xl, "links")
        history = _safe_read_sheet(xl, "history")
        if not info.empty:
            row = info.iloc[0]
            code = str(row.get("sifra") or row.get("code") or "")
            name = str(row.get("ime") or row.get("name") or code)
            vat = row.get("vat")
            if pd.isna(vat):
                vat = None
            else:
                vat = str(vat)
        else:
            code = folder_key
            name = folder_key
            vat = None
        sup = Supplier(code=code, name=name, vat=vat, links=links, history=history)
        suppliers[code] = sup
        name_index[folder_key] = sup
    return suppliers, name_index


def save_supplier(sup: Supplier, root: Path) -> Path:
    """Save ``sup`` into ``root`` directory and return the created file path."""
    folder = root / sanitize_folder_name(sup.vat or sup.name or sup.code)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "supplier.xlsx"

    info_df = pd.DataFrame([
        {"sifra": sup.code, "ime": sup.name, "vat": sup.vat}
    ])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        info_df.to_excel(writer, sheet_name="info", index=False)
        sup.links.to_excel(writer, sheet_name="links", index=False)
        sup.history.to_excel(writer, sheet_name="history", index=False)
    return path
