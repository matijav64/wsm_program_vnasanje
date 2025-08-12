from decimal import Decimal
from pathlib import Path

import pytest

from wsm.parsing.eslog import parse_eslog_invoice


def test_parse_eslog_invoice_allowance_with_vat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    xml_path = Path("tests/allowance_with_vat.xml")
    with caplog.at_level("WARNING"):
        df, ok = parse_eslog_invoice(xml_path)

    gross_total = (df["vrednost"] + df["ddv"]).sum().quantize(Decimal("0.01"))

    assert gross_total == Decimal("184.58")
    assert ok
    assert not any(
        "Invoice total mismatch" in rec.message for rec in caplog.records
    )
    assert not any(
        "Line VAT mismatch" in rec.message for rec in caplog.records
    )
