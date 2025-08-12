from decimal import Decimal
from wsm.parsing.eslog import (
    parse_eslog_invoice,
    extract_header_net,
    extract_total_tax,
    extract_grand_total,
)


def test_invoice_total_with_allowance(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE"\
"</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>100"\
"</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100"\
"</D_5004></C_C516></S_MOA>"
        "      <G_SG34>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>19.80"\
"</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>260</D_5025><D_5004>-10"\
"</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>19.80"\
"</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>109.80"\
"</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "allowance.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    net = extract_header_net(xml_path)
    vat = extract_total_tax(xml_path)
    grand = extract_grand_total(xml_path)

    assert ok
    assert (net + vat).quantize(Decimal("0.01")) == grand
