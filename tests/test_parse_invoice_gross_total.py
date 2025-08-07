from decimal import Decimal
from wsm.parsing.eslog import parse_invoice


def test_parse_invoice_extracts_gross_total():
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    df, header_total, discount_total, gross_total = parse_invoice(xml)
    assert header_total == Decimal("10")
    assert gross_total == Decimal("12.20")
    assert discount_total == Decimal("0")
    assert not df.empty

