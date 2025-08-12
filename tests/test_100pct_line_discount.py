from decimal import Decimal
from pathlib import Path
import logging

from wsm import analyze


def test_100pct_line_discount(tmp_path: Path, caplog) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item A</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>DISC</D_7140></C_C212></S_LIN>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>0</D_5118></C_C509></S_PRI>"
        "      <S_PRI><C_C509><D_5125>AAB</D_5125><D_5118>0</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>0</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      </G_SG39>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>8</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>8</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-2</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "invoice.xml"
    xml_path.write_text(xml)

    with caplog.at_level(logging.WARNING):
        df, header_total, ok = analyze.analyze_invoice(xml_path)

    assert ok
    assert "Line net mismatch" not in caplog.text
    assert "Invoice total mismatch" not in caplog.text

    doc_rows = df[df["sifra_dobavitelja"] == "_DOC_"]
    assert doc_rows["vrednost"].sum() == Decimal("-2")
    line_total = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    assert (line_total + doc_rows["vrednost"].sum()).quantize(Decimal("0.01")) == header_total

