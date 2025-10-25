from pathlib import Path
import pandas as pd
from wsm.core.supplier_store import Supplier, save_supplier, load_suppliers
from wsm.utils import sanitize_folder_name


def test_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "suppliers"
    links = pd.DataFrame({"code": ["A"], "name": ["Item"]})
    history = pd.DataFrame({"key": ["A_Item"], "cena": [1]})

    sup = Supplier(code="A", name="Acme", vat="SI123", links=links, history=history)
    save_supplier(sup, root)

    suppliers, idx = load_suppliers(root)
    assert set(suppliers) == {"A"}
    loaded = suppliers["A"]
    assert loaded.name == "Acme"
    assert loaded.vat == "SI123"
    pd.testing.assert_frame_equal(loaded.links, links)
    pd.testing.assert_frame_equal(loaded.history, history)

    safe = sanitize_folder_name("SI123")
    assert safe in idx
    assert idx[safe] is loaded
