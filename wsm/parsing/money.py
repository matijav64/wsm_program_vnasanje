from decimal import Decimal
import xml.etree.ElementTree as ET


def extract_total_amount(xml_root: ET.Element) -> Decimal:
    """
    Prebere osnovno glavo (InvoiceTotal) in, če obstaja,
    odšteje vrednost iz <DocumentDiscount>. Če <InvoiceTotal> ali
    <DocumentDiscount> manjkata, privzame 0.00.
    """
    # Preberemo tekst iz <InvoiceTotal>; če ga ni, vzamemo "0.00"
    base_str = xml_root.findtext("InvoiceTotal") or "0.00"
    # Preverimo, ali obstaja <DocumentDiscount>
    discount_str = xml_root.findtext("DocumentDiscount") or "0.00"

    # Pretvorimo čez Decimal (upoštevamo tudi vejice, če so bile v XML)
    base = Decimal(base_str.replace(",", "."))
    discount = Decimal(discount_str.replace(",", "."))

    return (base - discount).quantize(Decimal("0.01"))


def extract_line_items(xml_root: ET.Element):
    """
    Iz <LineItems> izriše DataFrame z naslednjimi stolpci:
      - cena_netto (Decimal)
      - kolicina (Decimal)
      - rabata_pct (Decimal)
    in izračuna 'izracunana_vrednost' = cena_netto * kolicina * (1 - rabata_pct/100).
    """
    import pandas as pd

    rows = []
    for li in xml_root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        izracun_val = (cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))).quantize(
            Decimal("0.01")
        )

        rows.append(
            {
                "cena_netto": float(cena),
                "kolicina": float(kolic),
                "rabata_pct": float(rabata_pct),
                "izracunana_vrednost": float(izracun_val),
            }
        )

    df = pd.DataFrame(rows)
    return df


def validate_invoice(df, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izračunanih vrstičnih vrednosti ujema s header_total
    (upoštevena že obdelana vrednost iz extract_total_amount). Toleranca 0.05 €.
    """
    from decimal import Decimal

    # Privzeti tolerance vrednosti
    tolerance = Decimal("0.05")

    # Pretvorimo nazaj v Decimal in izračunamo vsoto
    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    line_sum = df["izracunana_vrednost"].sum().quantize(Decimal("0.01"))

    return abs(line_sum - header_total) < tolerance
