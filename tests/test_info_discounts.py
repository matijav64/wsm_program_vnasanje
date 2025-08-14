from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice, parse_invoice


def test_info_discounts(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item A</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>8</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <G_SG42>"
        "          <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>"
        "        </G_SG42>"
        "      </G_SG39>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0002</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item B</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>5</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>5</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>13</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "inv.xml"
    path.write_text(xml)

    df, ok = parse_eslog_invoice(path)
    assert ok
    assert df.attrs.get("info_discounts")
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty
    assert df["rabata"].sum() == Decimal("0")

    df_cli, header_total, discount_total, _ = parse_invoice(path)
    assert header_total == Decimal("13")
    assert discount_total == Decimal("0")
