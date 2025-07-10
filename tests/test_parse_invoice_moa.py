from decimal import Decimal
from wsm.parsing.eslog import parse_invoice


def test_parse_invoice_uses_moa_203_value():
    xml = (
        "<Invoice>"
        "  <InvoiceTotal>130.00</InvoiceTotal>"
        "  <LineItems>"
        "    <LineItem>"
        "      <Quantity>2</Quantity>"
        "      <S_MOA>"
        "        <C_C516>"
        "          <D_5025>203</D_5025>"
        "          <D_5004>50.00</D_5004>"
        "        </C_C516>"
        "      </S_MOA>"
        "    </LineItem>"
        "    <LineItem>"
        "      <PriceNet>40.00</PriceNet>"
        "      <Quantity>2</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "  </LineItems>"
        "</Invoice>"
    )
    df, header_total, discount_total = parse_invoice(xml)
    assert header_total == Decimal("130.00")
    assert discount_total == Decimal("0")
    assert list(df["izracunana_vrednost"]) == [Decimal("50.00"), Decimal("80.00")]
    assert list(df["cena_netto"]) == [Decimal("25.0000"), Decimal("40.00")]
    assert df["rabata_pct"].tolist() == [Decimal("0"), Decimal("0")]
