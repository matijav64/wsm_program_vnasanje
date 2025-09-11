import pandas as pd
from decimal import Decimal

from wsm import utils


def test_log_price_history_ignores_returns(tmp_path):
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["A", "A"],
            "naziv": ["Artikel", "Artikel"],
            "cena_netto": [Decimal("10"), Decimal("10")],
            "total_net": [Decimal("200"), Decimal("-200")],
            "kolicina_norm": [Decimal("20"), Decimal("-20")],
            "enota_norm": ["kos", "kos"],
        }
    )

    hist_file = tmp_path / "dummy.xlsx"
    utils.log_price_history(df, hist_file, suppliers_dir=tmp_path)

    out = pd.read_excel(tmp_path / "A" / "price_history.xlsx")
    assert len(out) == 1
    assert out.iloc[0]["line_netto"] == 10
