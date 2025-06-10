from decimal import Decimal
from pathlib import Path

from wsm import analyze
from wsm.parsing.eslog import extract_header_net


def test_analyze_invoice_merges_duplicates():
    path = Path("tests/CUSTOMERINVOICES_2025-04-01T14-29-47_2081078.xml")
    df, total, ok = analyze.analyze_invoice(path)

    # Item 00002122 appears three times with the same discount; should be merged
    row = df[(df["sifra_artikla"] == "00002122") & (df["rabata_pct"] == Decimal("4.99"))].iloc[0]
    assert row["kolicina"] == Decimal("72.00")
    assert row["vrednost"] == Decimal("50.25")

    # Another repeated item with weight normalization
    row2 = df[(df["sifra_artikla"] == "5998710960798") & (df["rabata_pct"] == Decimal("5.04"))].iloc[0]
    assert row2["kolicina"] == Decimal("3.200")
    assert row2["vrednost"] == Decimal("23.76")

    # Ensure rebate column exists and is filled
    assert "rabata" in df.columns
    assert df["rabata"].isna().sum() == 0

    assert total == extract_header_net(path)
    assert ok
