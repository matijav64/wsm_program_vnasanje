from decimal import Decimal
from pathlib import Path
from lxml import etree

from wsm.parsing.eslog import (
    parse_eslog_invoice,
    parse_invoice,
    parse_invoice_totals,
)


def test_informational_line_discounts_totals() -> None:
    path = Path("tests/PR6167-Slika2.XML")
    df, ok = parse_eslog_invoice(path)
    assert ok
    assert df.attrs.get("info_discounts")
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty

    totals = parse_invoice_totals(etree.parse(path))
    assert totals["net"] == Decimal("8")
    assert totals["vat"] == Decimal("1.76")
    assert totals["gross"] == Decimal("9.76")

    assert df["vrednost"].sum() == totals["net"]
    assert df["ddv"].sum() == totals["vat"]
    assert (df["vrednost"] + df["ddv"]).sum() == totals["gross"]

    _, header_total, discount_total, gross_total = parse_invoice(path)
    assert discount_total == Decimal("0")
    assert header_total == totals["net"]
    assert gross_total == totals["gross"]


def test_real_line_discounts_totals() -> None:
    path = Path("tests/PR6159-Slika2.XML")
    df, ok = parse_eslog_invoice(path)
    assert ok
    assert not df.attrs.get("info_discounts")

    totals = parse_invoice_totals(etree.parse(path))
    assert totals["net"] == Decimal("-103.19")
    assert totals["vat"] == Decimal("-22.70")
    assert totals["gross"] == Decimal("-125.89")

    assert df["vrednost"].sum() == totals["net"]
    assert df["ddv"].sum() == totals["vat"]
    assert (df["vrednost"] + df["ddv"]).sum() == totals["gross"]

    _, header_total, discount_total, gross_total = parse_invoice(path)
    assert header_total == totals["net"]
    assert gross_total == totals["gross"]
    assert discount_total == Decimal("0")
