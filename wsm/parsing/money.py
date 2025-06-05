# File: wsm/parsing/money.py
from decimal import Decimal
import xml.etree.ElementTree as ET
import pandas as pd

def extract_total_amount(xml_root: ET.Element) -> Decimal:
    """
    Prebere osnovno glavo (<InvoiceTotal>) in, če obstaja, odšteje vrednost iz
    <DocumentDiscount>. Če katerikoli manjka, privzame 0.00.
    """
    # Preberemo <InvoiceTotal> in <DocumentDiscount> (če obstaja)
    base_str = xml_root.findtext("InvoiceTotal") or "0.00"
    discount_str = xml_root.findtext("DocumentDiscount") or "0.00"

    # Pretvorimo v Decimal (zamenjava vejice z decimalno piko)
    base = Decimal(base_str.replace(",", "."))
    discount = Decimal(discount_str.replace(",", "."))

    # Vrne (base - discount), zaokroženo na 2 decimalki
    return (base - discount).quantize(Decimal("0.01"))

def extract_line_items(xml_root: ET.Element) -> pd.DataFrame:
    """
    Iz <LineItems> vsak <LineItem> prebere 'PriceNet', 'Quantity', 'DiscountPct'
    in izračuna izracunana_vrednost = price_net * quantity * (1 - discount_pct/100).
    Vrne DataFrame s stolpci:
      - cena_netto (float)
      - kolicina   (float)
      - rabata_pct (float)
      - izracunana_vrednost (float)
    """
    rows = []
    for li in xml_root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        # Decimal pretvorba (zamenjava vejice z ".")
        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        # Izracun vrednosti vrstice
        izracun_val = (
            cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))
        ).quantize(Decimal("0.01"))

        rows.append({
            "cena_netto": float(cena),
            "kolicina": float(kolic),
            "rabata_pct": float(rabata_pct),
            "izracunana_vrednost": float(izracun_val),
        })

    return pd.DataFrame(rows)

def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izracunana_vrednost (Decimal) ujema z header_total
    znotraj tolerance 0.05 €.
    """
    # Pretvorimo stolpec iz float v Decimal
    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    line_sum = df["izracunana_vrednost"].sum().quantize(Decimal("0.01"))
    return abs(line_sum - header_total) < Decimal("0.05")
