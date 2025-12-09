#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wsm.parsing.eslog import (  # noqa: E402
    extract_grand_total,
    extract_header_net,
    extract_total_tax,
    parse_eslog_invoice,
)


def iter_invoices(root: Path) -> Iterable[Path]:
    for ext in ("*.xml", "*.XML"):
        for path in root.glob(ext):
            if path.is_file():
                yield path


def summarize_invoice(path: Path) -> dict[str, Decimal | bool]:
    df, ok = parse_eslog_invoice(path)
    info_codes = {"_DOC_", "DOC_CHG"}
    if "sifra_dobavitelja" in df.columns:
        df_main = df[~df["sifra_dobavitelja"].isin(info_codes)].copy()
    else:
        df_main = df
    net = df_main["vrednost"].sum() if "vrednost" in df_main.columns else Decimal("0")
    vat = df_main["ddv"].sum() if "ddv" in df_main.columns else Decimal("0")
    gross = net + vat

    header_net = extract_header_net(path)
    header_vat = extract_total_tax(path)
    header_gross = extract_grand_total(path)

    return {
        "ok": ok,
        "net": net,
        "vat": vat,
        "gross": gross,
        "header_net": header_net,
        "header_vat": header_vat,
        "header_gross": header_gross,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a summary table for eSLOG invoices in a folder.",
    )
    parser.add_argument(
        "directory", type=Path, nargs="?", default=Path("tests"), help="Folder with XML invoices",
    )
    args = parser.parse_args()

    rows = []
    for path in sorted(iter_invoices(args.directory)):
        summary = summarize_invoice(path)
        rows.append((path.name, summary))

    header = (
        "File",
        "Net(parser)",
        "VAT(parser)",
        "Gross(parser)",
        "Net(XML)",
        "VAT(XML)",
        "Gross(XML)",
        "Match",
    )
    print("\t".join(header))
    for name, data in rows:
        match = (
            data["net"].quantize(Decimal("0.01")) == data["header_net"].quantize(Decimal("0.01"))
            and data["vat"].quantize(Decimal("0.01")) == data["header_vat"].quantize(Decimal("0.01"))
            and data["gross"].quantize(Decimal("0.01")) == data["header_gross"].quantize(Decimal("0.01"))
        )
        print(
            "\t".join(
                [
                    name,
                    f"{data['net']:.2f}",
                    f"{data['vat']:.2f}",
                    f"{data['gross']:.2f}",
                    f"{data['header_net']:.2f}",
                    f"{data['header_vat']:.2f}",
                    f"{data['header_gross']:.2f}",
                    "DA" if match else "NE",
                ]
            )
        )


if __name__ == "__main__":
    main()
