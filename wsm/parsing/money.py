# File: wsm/parsing/money.py
from decimal import Decimal
import xml.etree.ElementTree as ET
import pandas as pd

def extract_total_amount(xml_root: ET.Element) -> Decimal:
    """
    Prebere osnovno glavo (InvoiceTotal) in, če obstaja,
    odšteje vrednost iz <DocumentDiscount>. Če <InvoiceTotal> ali
    <DocumentDiscount> manjkata, privzame 0.00.
    """
    base_str = xml_root.findtext("InvoiceTotal") or "0.00"
    discount_str = xml_root.findtext("DocumentDiscount") or "0.00"

    # Pretvorimo iz niza z vejico v Decimal
    base = Decimal(base_str.replace(",", "."))
    discount = Decimal(discount_str.replace(",", "."))

    return (base - discount).quantize(Decimal("0.01"))

def extract_line_items(xml_root: ET.Element) -> pd.DataFrame:
    """
    Iz <LineItems> izriše DataFrame z naslednjimi stolpci:
      - cena_netto (float)
      - kolicina (float)
      - rabata_pct (float)
      - izračunana_vrednost (float)
    """
    rows = []
    for li in xml_root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        # Pretvorimo v Decimal (upoštevamo morebitne vejice)
        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        # Izračun vrednosti vrstice
        izracun_val = (cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))).quantize(
            Decimal("0.01")
        )

        rows.append({
            "cena_netto": float(cena),
            "kolicina": float(kolic),
            "rabata_pct": float(rabata_pct),
            "izracunana_vrednost": float(izracun_val),
        })

    return pd.DataFrame(rows)

def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izračunanih vrstičnih vrednosti ujema z header_total
    (že upoštevana glava – popust). Toleranca je 0.05 €.
    """
    # Pretvorimo stolpec nazaj v Decimal za natančnost
    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    line_sum = df["izracunana_vrednost"].sum().quantize(Decimal("0.01"))

    return abs(line_sum - header_total) < Decimal("0.05")
