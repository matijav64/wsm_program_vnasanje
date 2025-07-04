import pandas as pd
from PyQt5 import QtCore
from wsm.ui_qt import DataFrameModel


def test_wsm_sifra_editable_based_on_status():
    df = pd.DataFrame({
        "wsm_sifra": ["111", "", "222"],
        "status": ["POVEZANO", "BONUS", pd.NA],
        "other": [1, 2, 3],
    })
    model = DataFrameModel(df)
    col = df.columns.get_loc("wsm_sifra")

    editable0 = model.flags(model.index(0, col)) & QtCore.Qt.ItemIsEditable
    editable1 = model.flags(model.index(1, col)) & QtCore.Qt.ItemIsEditable
    editable2 = model.flags(model.index(2, col)) & QtCore.Qt.ItemIsEditable

    assert not editable0
    assert editable1
    assert editable2

    # other column should never be editable
    other_col = df.columns.get_loc("other")
    assert not (model.flags(model.index(1, other_col)) & QtCore.Qt.ItemIsEditable)
