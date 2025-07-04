from __future__ import annotations

from PyQt5 import QtCore
import pandas as pd


class DataFrameModel(QtCore.QAbstractTableModel):
    """Simple Qt table model backed by a pandas DataFrame."""

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self._df = df

    def rowCount(self, parent: QtCore.QModelIndex | QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        return len(self._df)

    def columnCount(self, parent: QtCore.QModelIndex | QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        return len(self._df.columns)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        value = self._df.iat[index.row(), index.column()]
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole):
            if pd.isna(value):
                return ""
            return str(value)
        return None

    def setData(self, index: QtCore.QModelIndex, value, role: int = QtCore.Qt.EditRole):  # type: ignore[override]
        if role != QtCore.Qt.EditRole or not index.isValid():
            return False
        column = self._df.columns[index.column()]
        self._df.at[index.row(), column] = value
        self.dataChanged.emit(index, index, [QtCore.Qt.DisplayRole, QtCore.Qt.EditRole])
        return True

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if role != QtCore.Qt.DisplayRole:
            return None
        if orientation == QtCore.Qt.Horizontal:
            return str(self._df.columns[section])
        return str(section)

    def flags(self, index: QtCore.QModelIndex):  # type: ignore[override]
        if not index.isValid():
            return QtCore.Qt.NoItemFlags
        base = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        column_name = self._df.columns[index.column()]
        if column_name == "wsm_sifra":
            status = None
            if "status" in self._df.columns:
                status = self._df.at[index.row(), "status"]
            if pd.isna(status) or status != "POVEZANO":
                return base | QtCore.Qt.ItemIsEditable
        return base
