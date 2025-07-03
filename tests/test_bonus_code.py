import pandas as pd
from pathlib import Path
import json
from wsm.utils import povezi_z_wsm


def _setup_env(tmp_path: Path) -> Path:
    links_dir = tmp_path / "links" / "Test"
    links_dir.mkdir(parents=True)
    # empty manual links
    pd.DataFrame(columns=["sifra_dobavitelja", "naziv", "naziv_ckey", "wsm_sifra"]).to_excel(
        links_dir / "SUP_Test_povezane.xlsx", index=False
    )
    (links_dir / "supplier.json").write_text(json.dumps({"sifra": "SUP", "ime": "Test"}))
    return tmp_path / "links"


def test_bonus_code_applied(monkeypatch, tmp_path):
    links_dir = _setup_env(tmp_path)
    sifre = tmp_path / "sifre.xlsx"
    pd.DataFrame({"wsm_sifra": ["1"], "wsm_naziv": ["Dummy"]}).to_excel(sifre, index=False)
    keywords = tmp_path / "kw.xlsx"
    pd.DataFrame(columns=["wsm_sifra", "keyword"]).to_excel(keywords, index=False)

    df_items = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Gratis"],
        "is_gratis": [True],
    })

    monkeypatch.setenv("WSM_BONUS_CODE", "BON")

    result = povezi_z_wsm(df_items, str(sifre), str(keywords), links_dir, "SUP")

    assert result.loc[0, "wsm_sifra"] == "BON"
    assert result.loc[0, "status"] == "BONUS"

