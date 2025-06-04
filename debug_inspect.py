# debug_inspect.py
import pandas as pd
from decimal import Decimal

def inspect_debug(file_name, header_value):
    """
    Naloži DEBUG CSV, izračuna vsoto vrstic (stolpec 'vrednost') in primerja z glavo.
    Izpiše natančne vrstice in razliko.
    """
    df = pd.read_csv(file_name)

    # Preverimo, ali obstaja stolpec 'vrednost'
    if "vrednost" not in df.columns:
        print(f"V datoteki {file_name} ni stolpca 'vrednost'. Stolpci so: {df.columns.tolist()}")
        return

    # Pretvorimo vrednosti v Decimal (da natančno primerjamo)
    try:
        df["vrednost_decimal"] = df["vrednost"].apply(lambda x: Decimal(str(x)))
    except Exception as e:
        print(f"Napaka pri pretvorbi stolpca 'vrednost' v Decimal v {file_name}: {e}")
        return

    line_sum = df["vrednost_decimal"].sum().quantize(Decimal("0.01"))
    header = Decimal(str(header_value))

    print(f"\n--- Analiza {file_name} ---")
    print(f"Vsota vrstic (stolpec 'vrednost'): {line_sum} €, Glava: {header} €")
    print(f"Razlika: {line_sum - header} €\n")
    print("Prvih 10 vrstic (s stolpcema cena_netto, kolicina, vrednost):")
    cols_to_show = [col for col in ("cena_netto", "kolicina", "vrednost") if col in df.columns]
    print(df[cols_to_show].head(10))

    # Poiščemo nekaj vrstic z največjimi posameznimi vsotnimi razlikami glede na sorazmerni delež
    df["abs_diff_line"] = (df["vrednost_decimal"] - (header / len(df))).abs()
    top_diffs = df.sort_values("abs_diff_line", ascending=False).head(5)
    print("\nNekaj vrstic z največjimi odstotnimi razlikami (glede na enak delež glave):")
    print(top_diffs[cols_to_show + ["abs_diff_line"]])

if __name__ == "__main__":
    # Tu podajte poti do vaših DEBUG CSV-jev in glave (iz CLI-izpisa)
    inspect_debug("tests/debug/2025-581-racun_DEBUG.csv", "958.11")
    inspect_debug("tests/debug/PR5697-Slika2_DEBUG.csv", "266.94")
