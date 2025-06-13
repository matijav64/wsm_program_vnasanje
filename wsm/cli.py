# File: wsm/cli.py
import click
import pandas as pd
from pathlib import Path
from decimal import Decimal

from wsm.parsing.eslog import parse_invoice, validate_invoice, get_supplier_name
from wsm.parsing.pdf import parse_pdf, get_supplier_name_from_pdf
from wsm.parsing.money import detect_round_step, round_to_step
from wsm.utils import sanitize_folder_name
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
@click.option("--suppliers", type=click.Path(exists=True), default=None, help="Mapa z dobavitelji ali legacy suppliers.xlsx")
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

    from wsm.utils import main_supplier_code

    supplier_code = main_supplier_code(df) or "unknown"
    if invoice_path.suffix.lower() == ".xml":
        name = get_supplier_name(invoice_path) or supplier_code
    elif invoice_path.suffix.lower() == ".pdf":
        name = get_supplier_name_from_pdf(invoice_path) or supplier_code
    else:
        name = supplier_code
    safe_name = sanitize_folder_name(name)
    links_dir = Path("links") / safe_name
    links_dir.mkdir(parents=True, exist_ok=True)
    links_file = links_dir / f"{supplier_code}_{safe_name}_povezane.xlsx"

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


@main.command(name="override")
@click.argument("supplier_code")
@click.option(
    "--suppliers",
    type=click.Path(),
    default="links",
    help="Mapa z dobavitelji ali legacy suppliers.xlsx",
)
@click.option("--set", "override", flag_value=True, help="Omogoči pretvorbo H87 v kg")
@click.option("--unset", "override", flag_value=False, help="Onemogoči pretvorbo H87 v kg")
def override_cmd(supplier_code, suppliers, override):
    """Uredi nastavitev ``override_H87_to_kg`` za dobavitelja."""
    if override is None:
        click.echo("Uporabite --set ali --unset za nastavitev vrednosti.")
        return

    from wsm.ui.review_links import _load_supplier_map, _write_supplier_map

    sup_file = Path(suppliers)
    sup_map = _load_supplier_map(sup_file)
    info = sup_map.get(supplier_code, {"ime": supplier_code, "override_H87_to_kg": False})
    info["override_H87_to_kg"] = override
    sup_map[supplier_code] = info
    _write_supplier_map(sup_map, sup_file)
    click.echo(f"{supplier_code}: override_H87_to_kg = {override}")


@main.command(name="round-debug")
@click.argument("invoice", type=click.Path(exists=True))
def round_debug(invoice):
    """Prikaži podrobnosti o seštevanju vrstic in zaokroževanju."""
    df, header_total = parse_invoice(invoice)
    col = "izracunana_vrednost" if "izracunana_vrednost" in df.columns else "vrednost"
    line_sum_dec = Decimal(str(df.get(col, pd.Series(dtype=float)).sum()))
    step = detect_round_step(header_total, line_sum_dec)
    rounded = round_to_step(line_sum_dec, step)
    click.echo(f"Glava računa: {header_total} €")
    click.echo(f"Vsota vrstic: {line_sum_dec} €")

    click.echo(f"Razlika pred zaokrožitvijo: {header_total - line_sum_dec} €")

    click.echo(f"Zaznan korak zaokroževanja: {step}")
    click.echo(f"Vsota po zaokrožitvi: {rounded} €")
    click.echo(f"Razlika po zaokrožitvi: {header_total - rounded} €")

if __name__ == "__main__":
    main()
