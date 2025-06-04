import logging
import sys
from decimal import Decimal
from pathlib import Path

import click

from wsm.parsing.eslog import parse_invoice, validate_invoice

log = logging.getLogger("wsm.cli")


# ——— Simulirani izpis popustov (optional; po potrebi odstranite ali prilagodite) ———

neto_items = Decimal("958.11")
line_rebate = Decimal("53.82")
doc_rebate = Decimal("0.00")
total_rebate = line_rebate + doc_rebate
header_neto = Decimal("958.11")
has_line_discounts = True  # Nastavite glede na svojo logiko
neto_real = neto_items - total_rebate

log.info("\n============ POVZETEK POPUSTOV ============")

if has_line_discounts:
    log.info("Skupni NETO brez popustov : ni na voljo – cene že vsebujejo popuste")
else:
    log.info(f"Skupni NETO brez popustov : {(neto_items + line_rebate):,.2f} €")

log.info(f"Skupni POPUST            : {total_rebate:,.2f} € "
         f"({line_rebate:,.2f} € vrstični + {doc_rebate:,.2f} € dokument)")
log.info(f"Skupni NETO s popusti    : {neto_real:,.2f} € ← izračunano iz postavk")
log.info("===========================================\n")

if header_neto:
    znak = "✓" if abs(header_neto - neto_real) < Decimal("0.05") else "✗"
    log.info(f"Glava po popustu: {header_neto:.2f} €  "
             f"vs. Izračunano: {neto_real:.2f} € → {znak}")


# ——— Konfiguracija CLI z Click ———

@click.group()
def main():
    """
    WSM – orodje za parsanje in validacijo e-računov.

    Podkomande:
      validate <poti_do_mape>    Pregleda vse XML/PDF v mapi in izpiše OK/NESKLADJE.
      review <poti_do_links.xlsx> Zažene UI za pregled/povezovanje postavk (če obstaja).
    """
    pass


@main.command("validate")
@click.argument("input_folder", type=click.Path(exists=True, file_okay=False))
def validate_folder(input_folder):
    """
    Pregleda vse XML/PDF datoteke v mapi <input_folder> in izpiše:
      [OK]       <ime_datoteke>  : vrsticna_vsota == glava
      [NESKLADJE] <ime_datoteke> : vrsticna_vsota != glava
      [NAPAKA PARSANJA] <ime_datoteke> : težava pri branju/parsanju
    """
    folder = Path(input_folder)
    had_errors = False

    for file in folder.iterdir():
        suffix = file.suffix.lower()
        if suffix not in (".xml", ".pdf"):
            continue

        try:
            df, header_total, currency = parse_invoice(file)
        except Exception as e:
            click.secho(f"[NAPAKA PARSANJA] {file.name}: {e}", fg="red")
            had_errors = True
            continue

        is_valid = validate_invoice(df, header_total, currency)
        if not is_valid:
            click.secho(
                f"[NESKLADJE] {file.name}: vrsticna_vsota != glava({header_total})",
                fg="yellow",
            )
            # (opcijsko) shranite DataFrame za debug:
            debug_folder = folder / "debug"
            debug_folder.mkdir(exist_ok=True)
            df.to_csv(debug_folder / f"{file.stem}_DEBUG.csv", index=False)
            had_errors = True
        else:
            click.secho(
                f"[OK]      {file.name}: vse se ujema ({header_total} {currency})",
                fg="green",
            )

    if had_errors:
        sys.exit(1)


@main.command("review")
@click.argument("supplier_links", type=click.Path(exists=True, file_okay=True))
def review_links_cli(supplier_links):
    """
    Zažene UI za pregled in povezovanje postavk (če je modul wsm.ui.review_links prisoten).
    """
    try:
        from wsm.ui.review_links import review_links
    except ImportError:
        click.secho("Modul wsm.ui.review_links ni najden. Preveri, ali obstaja datoteka wsm/ui/review_links.py.", fg="red")
        sys.exit(1)

    review_links()


if __name__ == "__main__":
    main()
