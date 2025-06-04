import xml.etree.ElementTree as ET
from decimal import Decimal
import pandas as pd
from pathlib import Path

# Uvozimo funkcije iz money.py
from wsm.parsing.money import extract_total_amount, validate_invoice as validate_line_values

def parse_invoice(source):
    """
    Parsira e-račun iz XML-poteka ali iz niza XML-besedila.
    Vrne (DataFrame, header_total), kjer DataFrame vsebuje
    stolpce: ['cena_netto', 'kolicina', 'rabata_pct', 'izracunana_vrednost'].
    header_total je Decimal z bruto glavo minus dokumentarni popust.
    """
    # Če source ni pot, poskusimo interpretirati kot XML niz
    if isinstance(source, (str, Path)) and Path(source).exists():
        tree = ET.parse(source)
        root = tree.getroot()
    else:
        # Predpostavimo, da je source niz XML-besedila
        root = ET.fromstring(source)

    # Header: preberemo glavo z upoštevanim dokumentarnim popustom
    header_total = extract_total_amount(root)

    # Parsamo vse vrstične elemente
    rows = []
    for li in root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        # Pretvorimo v Decimal (upoštevamo možne vejice namesto pik)
        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        # Izračunana vrstična vrednost
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
    return df, header_total


def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """
    Preveri, ali se vsota vseh izračunanih vrstičnih vrednosti ujema z header_total
    znotraj tolerance 0.05 €.
    """
    # Pretvorimo stolpec iz float nazaj v Decimal
    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(lambda x: Decimal(str(x)))
    return validate_line_values(df, header_total)
