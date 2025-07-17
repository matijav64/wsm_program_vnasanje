"""PyQt alternative for :mod:`wsm.ui.review.gui`.

This module provides the ``review_links_qt`` function with an interface
similar to the Tkinter version.  The GUI is simplified but keeps the
same workflow: edit WSM names for each invoice line, review the
summary and save the links to ``links_file`` using the same helper
functions as the original implementation.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import logging
import sys

import pandas as pd

try:
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore
except Exception as exc:  # pragma: no cover - optional dependency
    raise ImportError("PyQt5 is required for the Qt GUI") from exc

from wsm.constants import PRICE_DIFF_THRESHOLD
from wsm.ui.review.helpers import (
    _fmt,
    _norm_unit,
    _split_totals,
    _apply_price_warning,
)
from wsm.ui.review.io import _save_and_close, _load_supplier_map
from wsm.parsing.money import detect_round_step
from wsm.utils import short_supplier_name, _build_header_totals

log = logging.getLogger(__name__)


class _WsmDelegate(QtWidgets.QStyledItemDelegate):
    """Delegate with a completer for the WSM name column."""

    def __init__(
        self,
        names: list[str],
        name_to_code: dict[str, str],
        code_col: int,
        parent=None,
    ):
        super().__init__(parent)
        self._names = names
        self._name_to_code = name_to_code
        self._code_col = code_col

    def createEditor(self, parent, option, index):  # noqa: D401 - Qt signature
        editor = QtWidgets.QLineEdit(parent)
        comp = QtWidgets.QCompleter(self._names, editor)
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        editor.setCompleter(comp)
        return editor

    def setEditorData(self, editor, index):  # noqa: D401 - Qt signature
        text = index.data(QtCore.Qt.EditRole) or ""
        editor.setText(str(text))

    def setModelData(self, editor, model, index):  # noqa: D401 - Qt signature
        text = editor.text()
        model.setData(index, text)
        code = self._name_to_code.get(text) or ""
        model.setData(model.index(index.row(), self._code_col), code)


def review_links_qt(
    df: pd.DataFrame,
    wsm_df: pd.DataFrame,
    links_file: Path,
    invoice_total: Decimal,
    invoice_path: Path | None = None,
    price_warn_pct: float | int | Decimal | None = None,
) -> pd.DataFrame:
    """Interactively map supplier invoice rows to WSM items using PyQt."""

    df = df.copy()
    price_warn_threshold = (
        Decimal(str(price_warn_pct))
        if price_warn_pct is not None
        else PRICE_DIFF_THRESHOLD
    )

    supplier_code = links_file.stem.split("_")[0]
    suppliers_file = links_file.parent.parent
    sup_map = _load_supplier_map(suppliers_file)
    supplier_info = sup_map.get(supplier_code, {})
    supplier_name = short_supplier_name(
        supplier_info.get("ime", supplier_code)
    )
    supplier_vat = supplier_info.get("vat")

    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    doc_discount_total = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"].reset_index(drop=True)

    header_totals = _build_header_totals(invoice_path, invoice_total)
    invoice_total = header_totals["net"]

    df["cena_pred_rabatom"] = df.apply(
        lambda r: (
            (r["vrednost"] + r["rabata"]) / r["kolicina"]
            if r["kolicina"]
            else Decimal("0")
        ),
        axis=1,
    )
    df["cena_po_rabatu"] = df.apply(
        lambda r: (
            r["vrednost"] / r["kolicina"] if r["kolicina"] else Decimal("0")
        ),
        axis=1,
    )
    df["rabata_pct"] = df.apply(
        lambda r: (
            (
                (r["rabata"] / (r["vrednost"] + r["rabata"])) * Decimal("100")
            ).quantize(Decimal("0.01"))
            if (r["vrednost"] + r["rabata"])
            else Decimal("0.00")
        ),
        axis=1,
    )
    df["total_net"] = df["vrednost"]
    df["is_gratis"] = df["rabata_pct"] >= Decimal("99.9")
    df["kolicina_norm"], df["enota_norm"] = zip(
        *[
            _norm_unit(Decimal(str(q)), u, n, vat, code)
            for q, u, n, vat, code in zip(
                df["kolicina"],
                df["enota"],
                df["naziv"],
                df["ddv_stopnja"],
                df.get("sifra_artikla"),
            )
        ]
    )
    # Keep ``kolicina_norm`` as ``Decimal`` to avoid losing precision in
    # subsequent calculations and when saving the file. Previously the column
    # was cast to ``float`` which could introduce rounding errors.

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QMainWindow()
    win.setWindowTitle(f"Ročna revizija – {supplier_name}")
    central = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(central)
    win.setCentralWidget(central)

    header = QtWidgets.QLabel(f"Dobavitelj: {supplier_name}")
    layout.addWidget(header)

    cols = [
        "naziv",
        "kolicina_norm",
        "enota_norm",
        "rabata_pct",
        "cena_pred_rabatom",
        "cena_po_rabatu",
        "total_net",
        "wsm_naziv",
        "wsm_sifra",
    ]
    table = QtWidgets.QTableWidget(len(df), len(cols))
    table.setHorizontalHeaderLabels(
        [
            "Naziv",
            "Količina",
            "Enota",
            "Rabat %",
            "Net. pred rab.",
            "Net. po rab.",
            "Skupna neto",
            "WSM naziv",
            "WSM šifra",
        ]
    )
    layout.addWidget(table)
    name_to_code = dict(zip(wsm_df["wsm_naziv"], wsm_df["wsm_sifra"]))
    names = list(name_to_code.keys())
    delegate = _WsmDelegate(names, name_to_code, cols.index("wsm_sifra"))
    table.setItemDelegateForColumn(cols.index("wsm_naziv"), delegate)

    for row, r in df.iterrows():
        values = [
            r["naziv"],
            _fmt(r["kolicina_norm"]),
            r["enota_norm"],
            _fmt(r["rabata_pct"]),
            _fmt(r["cena_pred_rabatom"]),
            _fmt(r["cena_po_rabatu"]),
            _fmt(r["total_net"]),
            r.get("wsm_naziv", ""),
            r.get("wsm_sifra", ""),
        ]
        for col, val in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(val))
            if col != cols.index("wsm_naziv"):
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            table.setItem(row, col, item)
        # price warning coloring
        try:
            from wsm.utils import load_last_price

            label = f"{r['sifra_dobavitelja']} - {r['naziv']}"
            prev_price = load_last_price(label, suppliers_file)
        except Exception:  # pragma: no cover - ignore IO errors
            prev_price = None
        warn, _ = _apply_price_warning(
            r["cena_po_rabatu"],
            prev_price,
            threshold=price_warn_threshold,
        )
        item = table.item(row, cols.index("cena_po_rabatu"))
        item.setBackground(QtGui.QColor("orange") if warn else QtGui.QBrush())

    summary = QtWidgets.QTableWidget()
    layout.addWidget(summary)
    total_label = QtWidgets.QLabel()
    layout.addWidget(total_label)

    def update_summary() -> None:
        for i in range(summary.rowCount()):
            summary.removeRow(0)
        required = {
            "wsm_sifra",
            "vrednost",
            "rabata",
            "kolicina_norm",
            "rabata_pct",
        }
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
            summary_df["neto_brez_popusta"] = (
                summary_df["vrednost"] + summary_df["rabata"]
            )
            summary_df["wsm_naziv"] = summary_df["wsm_sifra"].map(
                wsm_df.set_index("wsm_sifra")["wsm_naziv"]
            )
            summary.setColumnCount(6)
            summary.setHorizontalHeaderLabels(
                [
                    "WSM šifra",
                    "WSM naziv",
                    "Količina",
                    "Znesek",
                    "Rabat %",
                    "Neto po rabatu",
                ]
            )
            summary.setRowCount(len(summary_df))
            for r_i, sr in summary_df.iterrows():
                vals = [
                    sr["wsm_sifra"],
                    sr["wsm_naziv"],
                    _fmt(sr["kolicina_norm"]),
                    _fmt(sr["neto_brez_popusta"]),
                    _fmt(sr["rabata_pct"]),
                    _fmt(sr["vrednost"]),
                ]
                for c_i, v in enumerate(vals):
                    summary.setItem(
                        r_i, c_i, QtWidgets.QTableWidgetItem(str(v))
                    )

        linked_total, unlinked_total, total_sum = _split_totals(
            df, doc_discount_total
        )
        step_total = detect_round_step(header_totals["net"], total_sum)
        match_symbol = (
            "✓" if abs(total_sum - header_totals["net"]) <= step_total else "✗"
        )
        text = (
            f"Skupaj povezano: {_fmt(linked_total)} € + "
            f"Skupaj ostalo: {_fmt(unlinked_total)} € = "
            f"Skupni seštevek: {_fmt(total_sum)} € | "
            "Skupna vrednost računa: "
            f"{_fmt(header_totals['net'])} € {match_symbol}"
        )
        total_label.setText(text)

    update_summary()

    btn_layout = QtWidgets.QHBoxLayout()
    layout.addLayout(btn_layout)
    save_btn = QtWidgets.QPushButton("Shrani & zapri")
    exit_btn = QtWidgets.QPushButton("Izhod")
    btn_layout.addWidget(save_btn)
    btn_layout.addWidget(exit_btn)

    def gather_df() -> pd.DataFrame:
        for row in range(table.rowCount()):
            df.at[row, "wsm_naziv"] = (
                table.item(row, cols.index("wsm_naziv")).text().strip()
                or pd.NA
            )
            code = table.item(row, cols.index("wsm_sifra")).text().strip()
            df.at[row, "wsm_sifra"] = code or pd.NA
            df.at[row, "dobavitelj"] = supplier_name
        return pd.concat([df, df_doc], ignore_index=True)

    def on_save() -> None:
        new_df = gather_df()
        _save_and_close(
            new_df,
            pd.DataFrame(),
            wsm_df,
            links_file,
            win,
            supplier_name,
            supplier_code,
            sup_map,
            suppliers_file,
            invoice_path=invoice_path,
            vat=supplier_vat,
        )

    def on_exit() -> None:
        win.close()

    save_btn.clicked.connect(on_save)
    exit_btn.clicked.connect(on_exit)

    win.show()
    app.exec_()

    return gather_df()
