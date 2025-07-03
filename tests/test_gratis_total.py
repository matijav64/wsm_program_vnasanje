from decimal import Decimal
from pathlib import Path
import pandas as pd

from wsm.parsing.eslog import parse_eslog_invoice


def _calc_totals(xml_path: Path):
    df = parse_eslog_invoice(xml_path, {})
    df_doc = df[df["sifra_dobavitelja"] == "_DOC_"]
    doc_discount_total = df_doc["vrednost"].sum()
    df = df[df["sifra_dobavitelja"] != "_DOC_"].copy()
    df["total_net"] = df["vrednost"]
    df["is_gratis"] = df["rabata_pct"] >= Decimal("99.9")
    df["wsm_sifra"] = pd.NA
    df.loc[df["naziv"] == "Normal", "wsm_sifra"] = "X"

    valid = df[~df["is_gratis"]]
    linked_total = valid[valid["wsm_sifra"].notna()]["total_net"].sum() + doc_discount_total
    unlinked_total = valid[valid["wsm_sifra"].isna()]["total_net"].sum()
    return linked_total, unlinked_total


def test_gratis_line_excluded_from_totals(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Normal</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0002</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Gratis</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>5</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <S_PCD><C_C501><D_5482>100</D_5482></C_C501></S_PCD>"
        "        <G_SG42>"
        "          <S_MOA><C_C516><D_5025>204</D_5025><D_5004>5</D_5004></C_C516></S_MOA>"
        "        </G_SG42>"
        "      </G_SG39>"
        "    </G_SG26>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "gratis.xml"
    xml_path.write_text(xml)

    linked, unlinked = _calc_totals(xml_path)
    assert linked == Decimal("10")
    assert unlinked == Decimal("0")


