from decimal import Decimal

import pandas as pd

from wsm.ui.review.io import _write_excel_links


def test_write_excel_links_saves_multiplier(tmp_path, caplog):
    df = pd.DataFrame(
        [
            {
                "sifra_dobavitelja": "1",
                "naziv": "Item",
                "naziv_ckey": "item",
                "wsm_sifra": "A1",
                "dobavitelj": "Supp",
                "enota_norm": "kos",
                "multiplier": Decimal("2"),
            }
        ]
    )
    manual_old = pd.DataFrame()
    links_file = tmp_path / "links.xlsx"
    with caplog.at_level("DEBUG"):
        _write_excel_links(df, manual_old, links_file)
    saved = pd.read_excel(links_file)
    assert "multiplier" in saved.columns
    assert saved.loc[0, "multiplier"] == 2
    assert "Saving multipliers for 1 items" in caplog.text
    assert "Multiplier details" in caplog.text
