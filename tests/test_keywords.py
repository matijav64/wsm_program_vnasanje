import pandas as pd
from pathlib import Path

from wsm.utils import extract_keywords, povezi_z_wsm
import json


def _setup_manual_links(tmp_path: Path) -> Path:
    links_dir = tmp_path / "links"
    supplier_dir = links_dir / "Test"
    supplier_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "sifra_dobavitelja": ["SUP", "SUP", "SUP", "SUP"],
        "naziv": ["Coca Cola 1L", "Coca Cola Zero 2L", "Fanta Orange 1L", "Sprite 1L"],
        "naziv_ckey": ["", "", "", ""],
        "wsm_sifra": ["100", "100", "200", "300"],
    })
    path = supplier_dir / "SUP_Test_povezane.xlsx"
    df.to_excel(path, index=False)

    info = {"sifra": "SUP", "ime": "Test"}
    (supplier_dir / "supplier.json").write_text(json.dumps(info))

    return links_dir


def test_extract_keywords(tmp_path):
    links_dir = _setup_manual_links(tmp_path)
    keywords_path = tmp_path / "kljucne_besede_wsm_kode.xlsx"

    kw_df = extract_keywords(links_dir, keywords_path)
    assert keywords_path.exists()
    tokens = set(kw_df[kw_df["wsm_sifra"] == "100"]["keyword"].tolist())
    assert "coca" in tokens
    assert "cola" in tokens


def test_povezi_z_wsm_autolearn(tmp_path):
    links_dir = _setup_manual_links(tmp_path)
    sifre_path = tmp_path / "sifre_wsm.xlsx"
    pd.DataFrame({"wsm_sifra": ["100"], "wsm_naziv": ["Coca Cola"]}).to_excel(sifre_path, index=False)

    keywords_path = tmp_path / "kljucne_besede_wsm_kode.xlsx"  # non-existent

    df_items = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Coca Cola Zero Sugar 0.5L"],
    })

    result = povezi_z_wsm(df_items, str(sifre_path), str(keywords_path), links_dir, "SUP")
    assert result.loc[0, "wsm_sifra"] == "100"
    assert result.loc[0, "status"] == "KLJUCNA_BES"
    assert keywords_path.exists()


def test_povezi_z_wsm_reads_env(monkeypatch, tmp_path):
    links_dir = _setup_manual_links(tmp_path)
    sifre_path = tmp_path / "sifre_wsm.xlsx"
    pd.DataFrame({"wsm_sifra": ["100"], "wsm_naziv": ["Coca Cola"]}).to_excel(sifre_path, index=False)

    env_path = tmp_path / "env_keywords.xlsx"
    monkeypatch.setenv("WSM_KEYWORDS", str(env_path))

    df_items = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Coca Cola Zero Sugar 0.5L"],
    })

    result = povezi_z_wsm(df_items, str(sifre_path), links_dir=links_dir, supplier_code="SUP")
    assert result.loc[0, "wsm_sifra"] == "100"
    assert env_path.exists()


def test_povezi_z_wsm_default_path(monkeypatch, tmp_path):
    links_dir = _setup_manual_links(tmp_path)
    sifre_path = tmp_path / "sifre_wsm.xlsx"
    pd.DataFrame({"wsm_sifra": ["100"], "wsm_naziv": ["Coca Cola"]}).to_excel(sifre_path, index=False)

    monkeypatch.chdir(tmp_path)

    df_items = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Coca Cola Zero Sugar 0.5L"],
    })

    result = povezi_z_wsm(df_items, str(sifre_path), links_dir=links_dir, supplier_code="SUP")
    assert result.loc[0, "wsm_sifra"] == "100"
    assert (tmp_path / "kljucne_besede_wsm_kode.xlsx").exists()

