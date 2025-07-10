import pytest
from decimal import Decimal
from wsm.parsing.money import extract_total_amount
from wsm.parsing.eslog import parse_invoice

@pytest.mark.parametrize(
    "xml_str, expected",
    [
        # Osnovni primer: samo glava brez popusta
        ("<InvoiceTotal>100.00</InvoiceTotal>", Decimal("100.00")),

        # Primer z dokumentarnim popustom
        (
            "<InvoiceTotal>100.00</InvoiceTotal>"
            "<DocumentDiscount>10.00</DocumentDiscount>",
            Decimal("90.00"),
        ),

        # Primer z delčnimi popusti  (npr. če je glava 250.50, doc discount 50.50 => 200.00)
        (
            "<InvoiceTotal>250.50</InvoiceTotal>"
            "<DocumentDiscount>50.50</DocumentDiscount>",
            Decimal("200.00"),
        ),
    ],
)
def test_extract_total_amount(xml_str, expected):
    """
    Preveri, da extract_total_amount pravilno upošteva InvoiceTotal in DocumentDiscount.
    """
    from xml.etree import ElementTree as ET

    root = ET.fromstring(f"<Invoice>{xml_str}</Invoice>")
    result = extract_total_amount(root)
    assert result == expected


def test_parse_invoice_minimal():
    """
    Testira parse_invoice za osnovni primer, kjer ni dokumentarnega popusta.
    """
    xml = (
        "<Invoice>"
        "  <InvoiceTotal>150.00</InvoiceTotal>"
        "  <LineItems>"
        "    <LineItem>"
        "      <PriceNet>50.00</PriceNet>"
        "      <Quantity>1</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "    <LineItem>"
        "      <PriceNet>100.00</PriceNet>"
        "      <Quantity>1</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "  </LineItems>"
        "</Invoice>"
    )
    # V tem primeru je vsota vrstic (50 + 100) = 150, glava = 150
    df, header_total, discount_total = parse_invoice(xml)
    assert header_total == Decimal("150.00")
    assert discount_total == Decimal("0")
    # Seštevek izračunanih vrstic:
    assert sum(df["izracunana_vrednost"]) == Decimal("150.00")
    assert ok


def test_parse_invoice_with_line_and_doc_discount():
    """
    Testira primer, kjer so vrstični popusti in dodaten dokumentarni popust.
    """
    xml = (
        "<Invoice>"
        "  <InvoiceTotal>300.00</InvoiceTotal>"
        "  <DocumentDiscount>50.00</DocumentDiscount>"
        "  <LineItems>"
        "    <LineItem>"
        "      <PriceNet>100.00</PriceNet>"
        "      <Quantity>2</Quantity>"
        "      <DiscountPct>10.00</DiscountPct>"
        "    </LineItem>"
        "    <LineItem>"
        "      <PriceNet>100.00</PriceNet>"
        "      <Quantity>1</Quantity>"
        "      <DiscountPct>0.00</DiscountPct>"
        "    </LineItem>"
        "  </LineItems>"
        "</Invoice>"
    )
    # Izračun vrstic:
    #   1. vrstica: 100 * 2 * (1 - 0.10) = 180.00
    #   2. vrstica: 100 * 1 * (1 - 0.00) = 100.00
    # Skupaj vrstic = 280.00, glava = 300.00, doc discount = 50 => 300 - 50 = 250.00.
    # Ker vsota vrstic (280.00) + doc discount (50.00) = 330.00, kar se ne ujema z
    # glavo (300.00), parse_invoice vrne `header_total` po odštetem popustu (250.00)
    # in DataFrame z `izracunana_vrednost`.
    df, header_total, discount_total = parse_invoice(xml)
    assert header_total == Decimal("250.00")
    assert discount_total == Decimal("50.00")
    # Skupaj izračunanih vrstic:
    assert sum(df["izracunana_vrednost"]) == Decimal("280.00")
    assert ok
    # Potrdimo, da extract_total_amount vrne 250 (300 - 50):
    from xml.etree import ElementTree as ET

    root = ET.fromstring(xml)
    from wsm.parsing.money import extract_total_amount

    assert extract_total_amount(root) == Decimal("250.00")
