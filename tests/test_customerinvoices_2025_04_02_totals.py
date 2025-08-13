from decimal import Decimal
from pathlib import Path

from wsm.parsing.eslog import parse_eslog_invoice


def test_customerinvoices_2025_04_02_totals():
    xml_path = Path("tests/CUSTOMERINVOICES_2025-04-02T14-27-29_2082483.xml")
    df, ok = parse_eslog_invoice(xml_path)
    net = df["vrednost"].sum().quantize(Decimal("0.01"))
    vat = df["ddv"].sum().quantize(Decimal("0.01"))
    gross = (net + vat).quantize(Decimal("0.01"))

    assert abs(net - Decimal("468.76")) <= Decimal("0.01")
    assert vat == Decimal("74.85")
    assert gross == Decimal("543.61")
    assert ok
