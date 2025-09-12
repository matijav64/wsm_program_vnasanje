from decimal import Decimal
import pandas as pd


def test_rabata_pct_decimal_safe():
    df = pd.DataFrame({"vrednost": [100.0, 0.0], "rabata": [20.0, 5.0]})
    for c in ("vrednost", "rabata"):
        df[c] = df[c].apply(
            lambda x: x if isinstance(x, Decimal) else Decimal(str(x))
        )
    df["rabata_pct"] = df.apply(
        lambda r: (
            (
                r["rabata"] / (r["vrednost"] + r["rabata"]) * Decimal("100")
            ).quantize(Decimal("0.01"))

            if r["vrednost"] != 0 and (r["vrednost"] + r["rabata"]) != 0

            else Decimal("0")
        ),
        axis=1,
    )
    assert list(df["rabata_pct"]) == [Decimal("16.67"), Decimal("0")]
