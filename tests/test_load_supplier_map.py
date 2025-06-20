import pandas as pd
import json
from pathlib import Path
from wsm.ui.review_links import _load_supplier_map


def test_load_supplier_map_from_folders(tmp_path: Path):
    links_dir = tmp_path / "links"
    links_dir.mkdir()

    # folder without supplier.json, only povezane file
    sup_a = links_dir / "Kvibo"
    sup_a.mkdir()
    df = pd.DataFrame({"sifra_dobavitelja": ["A"], "naziv": ["x"], "wsm_sifra": ["1"]})
    df.to_excel(sup_a / "KVIBO_povezane.xlsx", index=False)

    # folder with supplier.json and VAT
    sup_b = links_dir / "SI123"
    sup_b.mkdir()
    info = {"sifra": "ACM", "ime": "Acme Corp", "vat": "SI123"}
    (sup_b / "supplier.json").write_text(json.dumps(info))

    result = _load_supplier_map(links_dir)

    assert set(result) == {"KVIBO", "ACM"}
    assert result["KVIBO"]["ime"] == "Kvibo"
    assert result["ACM"]["ime"] == "Acme Corp"
    assert result["ACM"]["vat"] == "SI123"
