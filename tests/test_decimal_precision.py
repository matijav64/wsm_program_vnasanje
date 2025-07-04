from decimal import Decimal
from wsm.parsing.eslog import parse_invoice


def test_parse_invoice_high_precision_values():
    xml = (
        "<Invoice>"
        "  <InvoiceTotal>131.9999</InvoiceTotal>"
        "  <LineItems>"
        "    <LineItem>"
        "      <PriceNet>50.5555</PriceNet>"
        "      <Quantity>1</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "    <LineItem>"
        "      <PriceNet>81.4444</PriceNet>"
        "      <Quantity>1</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "  </LineItems>"
        "</Invoice>"
    )
    df, header_total, discount_total = parse_invoice(xml)
    assert header_total == Decimal("132.00")
    assert discount_total == Decimal("0")
    assert sum(df["izracunana_vrednost"]) == Decimal("132.00")
