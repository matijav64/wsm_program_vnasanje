import pytest
pytest.importorskip("openpyxl")
import pandas as pd
from pathlib import Path
from wsm.ui.review.io import _load_supplier_map


def test_load_supplier_map_from_history(tmp_path: Path) -> None:
    links_dir = tmp_path / "links"
    links_dir.mkdir()
    hist_folder = links_dir / "HistOnly"
    hist_folder.mkdir()
    df = pd.DataFrame({
        "code": ["H1"],
        "name": ["Item"],
        "cena": [1],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df.to_excel(hist_folder / "price_history.xlsx", index=False)

    result = _load_supplier_map(links_dir)

    assert "H1" in result
    assert result["H1"]["ime"] == "HistOnly"


def test_load_supplier_map_skips_empty_history(tmp_path: Path) -> None:
    links_dir = tmp_path / "links"
    links_dir.mkdir()
    hist_folder = links_dir / "EmptyHist"
    hist_folder.mkdir()
    pd.DataFrame(columns=["code", "name", "cena", "time"]).to_excel(
        hist_folder / "price_history.xlsx", index=False
    )

    result = _load_supplier_map(links_dir)

    assert result == {}
