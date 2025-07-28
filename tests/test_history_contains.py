import pytest

pytest.importorskip("openpyxl")
from pathlib import Path
import pandas as pd

from wsm.utils import history_contains


def test_history_contains_found(tmp_path: Path) -> None:
    path = tmp_path / "price_history.xlsx"
    pd.DataFrame({"invoice_id": ["1", "2"]}).to_excel(path, index=False)
    assert history_contains("1", path)
    assert history_contains("2", str(path))


def test_history_contains_missing(tmp_path: Path) -> None:
    path = tmp_path / "price_history.xlsx"
    pd.DataFrame({"invoice_id": ["1"]}).to_excel(path, index=False)
    assert not history_contains("2", path)


def test_history_contains_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "absent.xlsx"
    assert not history_contains("1", path)
