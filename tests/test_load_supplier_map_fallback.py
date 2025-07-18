from pathlib import Path
from wsm.ui.review.io import _load_supplier_map
from wsm.utils import sanitize_folder_name


def test_load_supplier_map_folder_name(tmp_path: Path):
    links_dir = tmp_path / "links"
    links_dir.mkdir()
    sup_folder = links_dir / "Acme Test"
    sup_folder.mkdir()

    result = _load_supplier_map(links_dir)
    code = sanitize_folder_name("Acme Test")
    assert code in result
    assert result[code]["ime"] == "Acme Test"
