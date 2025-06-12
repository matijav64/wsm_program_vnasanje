import pandas as pd
from decimal import Decimal
from wsm.ui.review_links import _norm_unit


def test_override_h87_ignores_old_units():
    df = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "naziv": ["Item"],
        "kolicina": [Decimal("2")],
        "enota": ["H87"],
    })
    manual_old = pd.DataFrame({
        "sifra_dobavitelja": ["SUP"],
        "enota_norm": ["kos"],
    })

    old_unit_dict = manual_old.set_index("sifra_dobavitelja")["enota_norm"].to_dict()

    df["kolicina_norm"], df["enota_norm"] = zip(
        *[
            _norm_unit(Decimal(str(q)), u, n, True)
            for q, u, n in zip(df["kolicina"], df["enota"], df["naziv"])
        ]
    )

    if old_unit_dict:
        def _restore_unit(r):
            if True and str(r["enota"]).upper() == "H87":
                return r["enota_norm"]
            return old_unit_dict.get(r["sifra_dobavitelja"], r["enota_norm"])
        df["enota_norm"] = df.apply(_restore_unit, axis=1)

    assert df.loc[0, "enota_norm"] == "kg"
