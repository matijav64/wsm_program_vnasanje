# File: wsm/cli.py
import click
import pandas as pd
from pathlib import Path
from decimal import Decimal

from wsm.parsing.eslog import parse_invoice, validate_invoice
from wsm.parsing.pdf import parse_pdf
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
@click.argument("invoice", type=click.Path(exists=True))
@click.option(
    "--wsm-codes",
    type=click.Path(exists=True),
    default=None,
    help="Pot do sifre_wsm.xlsx",
)
def review(invoice, wsm_codes):
    """Odpri GUI za ročno povezovanje WSM šifer."""
    try:
        from wsm.ui.review_links import review_links
    except ImportError as ie:
        click.echo(f"[NAPAKA] Ne morem uvoziti funkcije review_links: {ie}")
        return

    invoice_path = Path(invoice)
    try:
        if invoice_path.suffix.lower() == ".xml":
            df, total, _ = analyze_invoice(str(invoice_path))
        elif invoice_path.suffix.lower() == ".pdf":
            df = parse_pdf(str(invoice_path))
            total = df.get("vrednost", pd.Series(dtype=float)).sum()
            if "rabata" not in df.columns:
                df["rabata"] = Decimal("0")
        else:
            click.echo(f"[NAPAKA] Nepodprta datoteka: {invoice}")
            return
    except Exception as e:
        click.echo(f"[NAPAKA PARSANJA] {e}")
        return

    supplier_code = df["sifra_dobavitelja"].iloc[0] if not df.empty else "unknown"
    links_dir = Path("links")
    links_dir.mkdir(exist_ok=True)
    links_file = links_dir / f"{supplier_code}_povezave.xlsx"

    sifre_path = Path(wsm_codes) if wsm_codes else Path("sifre_wsm.xlsx")
    if sifre_path.exists():
        try:
            wsm_df = pd.read_excel(sifre_path, dtype=str)
        except Exception as exc:
            click.echo(f"[NAPAKA] Napaka pri branju {sifre_path}: {exc}")
            wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])
    else:
        if wsm_codes:
            click.echo(f"[NAPAKA] Datoteka {sifre_path} ne obstaja.")
        wsm_df = pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"])

    try:
        review_links(df, wsm_df, links_file, total, Path(invoice))
    except Exception as e:
        click.echo(f"[NAPAKA GUI] {e}")

if __name__ == "__main__":
    main()
