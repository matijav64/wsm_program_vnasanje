# File: wsm/parsing/money.py
from decimal import Decimal
import xml.etree.ElementTree as ET
import pandas as pd

def extract_total_amount(xml_root: ET.Element) -> Decimal:
    """
    Prebere osnovno glavo (<InvoiceTotal>) in, če obstaja, odšteje vrednost iz
    <DocumentDiscount>. Če katerikoli manjka, privzame 0.00.
    """
    base_str = xml_root.findtext("InvoiceTotal") or "0.00"
    discount_str = xml_root.findtext("DocumentDiscount") or "0.00"

    base = Decimal(base_str.replace(",", "."))
    discount = Decimal(discount_str.replace(",", "."))

    return (base - discount).quantize(Decimal("0.01"))

def extract_line_items(xml_root: ET.Element) -> pd.DataFrame:
    """
    Iz <LineItems> vsak <LineItem> prebere 'PriceNet', 'Quantity', 'DiscountPct'
    in izračuna izracunana_vrednost = price_net * quantity * (1 - discount_pct/100).
    Vrne DataFrame s stolpci:
      - cena_netto (Decimal)
      - kolicina   (Decimal)
      - rabata_pct (Decimal)
      - izracunana_vrednost (Decimal)
    """
    rows = []
    for li in xml_root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        izracun_val = (
            cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))
        ).quantize(Decimal("0.01"))

        rows.append({
            "cena_netto": cena,
            "kolicina": kolic,
            "rabata_pct": rabata_pct,
            "izracunana_vrednost": izracun_val,
        })

    return pd.DataFrame(rows, dtype=object)

def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izracunana_vrednost (Decimal) ujema z header_total
    znotraj tolerance 0.05 €.
    """
    # 1) Pretvorimo stolpec iz float v Decimal (če obstaja)
    if "izracunana_vrednost" not in df.columns:
        return False

    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))

    # 2) Vsoto pretvorimo v Decimal, četudi je sum() vrnil int
    total_sum = df["izracunana_vrednost"].sum()
    # Če sum vrne int ali float, ga pretvorimo v Decimal; če je že Decimal, OK
    if not isinstance(total_sum, Decimal):
        total_sum = Decimal(str(total_sum))
    line_sum = total_sum.quantize(Decimal("0.01"))

    # 3) Primerjamo z header_total
    return abs(line_sum - header_total) < Decimal("0.05")
