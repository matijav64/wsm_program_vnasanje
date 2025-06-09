# File: wsm/cli.py
import click
from pathlib import Path
from decimal import Decimal

from wsm.parsing.eslog import parse_invoice, validate_invoice
from wsm.analyze import analyze_invoice

@click.group()
def main():
    """WSM – CLI za validacijo in pregled e-računov."""
    pass

@main.command()
@click.argument("invoices", type=click.Path(exists=True), nargs=-1)
def validate(invoices):
    """
    Validiraj enega ali več e-računov (XML/PDF). 
    Podatki so lahko datoteke ali mape, CLI rekurzivno poišče vse *.xml v mapah.
    """
    if not invoices:
        click.echo("Prosim, navedite pot do vsaj ene datoteke ali mape.")
        return

    for path_str in invoices:
        path = Path(path_str)
        if path.is_dir():
            # Poiščemo vse .xml v mapi
            for xml_file in sorted(path.glob("*.xml")):
                _validate_file(xml_file)
        else:
            _validate_file(path)

def _validate_file(file_path: Path):
    """
    Poskrbi za validacijo posamezne datoteke: 
    - parse_invoice -> DataFrame in glava
    - validate_invoice -> True/False
    - Izpiše [OK], [NESKLADJE] ali [NAPAKA PARSANJA]
    """
    filename = file_path.name
    try:
        # parse_invoice vrača točno dva rezultata: (df, header_total)
        df, header_total = parse_invoice(str(file_path))
    except Exception as e:
        click.echo(f"[NAPAKA PARSANJA] {filename}: {e}")
        return

    try:
        ok = validate_invoice(df, header_total)
    except Exception as e:
        click.echo(f"[NAPAKA VALIDACIJE] {filename}: {e}")
        return

    if ok:
        click.echo(f"[OK]      {filename}: vse se ujema ({header_total:.2f} €)")
    else:
        click.echo(f"[NESKLADJE] {filename}: vrsticna_vsota != glava({header_total:.2f} €)")


@main.command()
@click.argument("invoice", type=click.Path(exists=True))
@click.option("--suppliers", type=click.Path(exists=True), default=None, help="Pot do suppliers.xlsx")
def analyze(invoice, suppliers):
    """Prikaži združene postavke in preveri seštevek."""
    df, total, ok = analyze_invoice(invoice, suppliers)
    click.echo(df.to_string(index=False))
    status = "OK" if ok else "NESKLADJE"
    click.echo(f"{status}: vsota vrstic {total:.2f} €")

@main.command()
@click.argument("supplier_file", type=click.Path(exists=True))
def review(supplier_file):
    """
    Zaženi GUI za povezovanje šifer glede na dobavnice. 
    supplier_file mora biti pot do suppliers.xlsx.
    """
    try:
        from wsm.ui.review_links import review_links
    except ImportError as ie:
        click.echo(f"[NAPAKA] Ne morem uvoziti funkcije review_links: {ie}")
        return

    try:
        review_links(supplier_file)
    except Exception as e:
        click.echo(f"[NAPAKA GUI] {e}")

if __name__ == "__main__":
    main()
