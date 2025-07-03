# File: wsm/cli.py
import click
import pandas as pd
import os
from pathlib import Path
from decimal import Decimal
import logging

from wsm.parsing.eslog import parse_invoice, validate_invoice, get_supplier_name
from wsm.parsing.pdf import parse_pdf, get_supplier_name_from_pdf
from wsm.parsing.money import detect_round_step, round_to_step
from wsm.utils import sanitize_folder_name, _load_supplier_map
from wsm.analyze import analyze_invoice

@click.group()
def main():
    """WSM – CLI za validacijo in pregled e-računov."""
    logging.basicConfig(level=logging.INFO)
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
@click.option("--suppliers", type=click.Path(), default=None, help="Mapa z dobavitelji ali legacy suppliers.xlsx")
def analyze(invoice, suppliers):
    """Prikaži združene postavke in preveri seštevek."""
    suppliers_path = suppliers or os.getenv("WSM_SUPPLIERS", "links")
    df, total, ok = analyze_invoice(invoice, suppliers_path)
    click.echo(df.to_string(index=False))
    status = "OK" if ok else "NESKLADJE"
    click.echo(f"{status}: vsota vrstic {total:.2f} €")

@main.command()
@click.argument("invoice", type=click.Path(exists=True))
@click.option(
    "--wsm-codes",
    type=click.Path(),
    default=None,
    help="Pot do sifre_wsm.xlsx",
)
@click.option(
    "--suppliers",
    type=click.Path(),
    default=None,
    help="Mapa z dobavitelji ali legacy suppliers.xlsx",
)
@click.option(
    "--keywords",
    type=click.Path(),
    default=None,
    help="Pot do kljucne_besede_wsm_kode.xlsx",
)
@click.option(
    "--price-warn-pct",
    type=float,
    default=None,
    help="Prag za opozorilo pri spremembi cene (v odstotkih)",
)
@click.option(
    "--use-pyqt",
    is_flag=True,
    default=False,
    help="Uporabi PyQt GUI namesto Tkinterja, če je na voljo",
)
def review(invoice, wsm_codes, suppliers, keywords, price_warn_pct, use_pyqt):
    """Odpri GUI za ročno povezovanje WSM šifer."""
    try:
        if use_pyqt:
            from wsm.ui_qt.review_links_qt import review_links_qt as review_links
        else:
            from wsm.ui.review.gui import review_links
    except ImportError as ie:
        click.echo(f"[NAPAKA] Ne morem uvoziti GUI-ja: {ie}")
        return

    invoice_path = Path(invoice)
    suppliers_path = suppliers or os.getenv("WSM_SUPPLIERS", "links")
    sifre_path = Path(wsm_codes) if wsm_codes else Path(os.getenv("WSM_CODES", "sifre_wsm.xlsx"))
    keywords_path = Path(keywords) if keywords else Path(os.getenv("WSM_KEYWORDS", "kljucne_besede_wsm_kode.xlsx"))
    try:
        if invoice_path.suffix.lower() == ".xml":
            df, total, _ = analyze_invoice(str(invoice_path), suppliers_path)
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
    sup_map = _load_supplier_map(Path(suppliers_path))
    map_vat = sup_map.get(supplier_code, {}).get("vat") if sup_map else None
    vat = None
    if invoice_path.suffix.lower() == ".xml":
        from wsm.parsing.eslog import get_supplier_info_vat

        name = get_supplier_name(invoice_path) or supplier_code
        _, _, vat_num = get_supplier_info_vat(invoice_path)
        if vat_num:
            vat = vat_num
    elif invoice_path.suffix.lower() == ".pdf":
        name = get_supplier_name_from_pdf(invoice_path) or supplier_code
    else:
        name = supplier_code
    if not vat and map_vat:
        vat = map_vat

    safe_id = sanitize_folder_name(vat or name)
    base = Path(suppliers_path)

    # Če obstaja stara mapa (npr. "unknown") za isto sifro, jo uporabimo,
    # da se ob shranjevanju prenesejo vse datoteke.
    old_info = sup_map.get(supplier_code)
    old_folder = (
        sanitize_folder_name(old_info.get("vat") or old_info.get("ime", ""))
        if old_info
        else ""
    )
    if old_info and old_folder != safe_id and (base / old_folder).exists():
        links_dir = base / old_folder
        links_file = links_dir / f"{supplier_code}_{old_folder}_povezane.xlsx"
    else:
        links_dir = base / safe_id
        links_dir.mkdir(parents=True, exist_ok=True)
        links_file = links_dir / f"{supplier_code}_{safe_id}_povezane.xlsx"

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
        from wsm.utils import povezi_z_wsm
        bonus_code = os.getenv("WSM_BONUS_CODE")
        if bonus_code:
            df = povezi_z_wsm(
                df,
                str(sifre_path),
                str(keywords_path),
                Path(suppliers_path),
                supplier_code,
                bonus_code=bonus_code,
            )
        else:
            df = povezi_z_wsm(
                df,
                str(sifre_path),
                str(keywords_path),
                Path(suppliers_path),
                supplier_code,
            )
    except Exception as exc:
        click.echo(f"[NAPAKA] Samodejno povezovanje ni uspelo: {exc}")

    try:
        review_links(
            df,
            wsm_df,
            links_file,
            total,
            Path(invoice),
            price_warn_pct,
        )
    except Exception as e:
        click.echo(f"[NAPAKA GUI] {e}")




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
