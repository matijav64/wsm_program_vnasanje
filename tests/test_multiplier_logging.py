from decimal import Decimal

import pandas as pd
import pytest

import wsm.ui.review.gui as rl


def _df():
    return pd.DataFrame(
        {
            "sifra_dobavitelja": ["1"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kos"],
            "vrednost": [Decimal("10")],
            "rabata": [Decimal("0")],
            "ddv": [Decimal("0")],
            "ddv_stopnja": [Decimal("0")],
            "sifra_artikla": [pd.NA],
        }
    )


def test_review_links_logs_multiplier(monkeypatch, tmp_path, caplog):
    links_file = tmp_path / "sup" / "code" / "links.xlsx"
    links_file.parent.mkdir(parents=True)
    manual_old = pd.DataFrame(
        {
            "sifra_dobavitelja": ["1"],
            "naziv": ["Item"],
            "wsm_sifra": [""],
            "dobavitelj": [""],
            "naziv_ckey": ["item"],
            "enota_norm": ["kos"],
            "multiplier": [2],
        }
    )
    manual_old.to_excel(links_file, index=False)

    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr(rl, "_build_header_totals", lambda *a, **k: {})
    monkeypatch.setattr(rl.tk, "Tk", lambda: (_ for _ in ()).throw(RuntimeError))

    with caplog.at_level("DEBUG"):
        with pytest.raises(RuntimeError):
            rl.review_links(
                _df(),
                pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"]),
                links_file,
                Decimal("10"),
            )

    assert "Applying multipliers for 1 rows" in caplog.text
    assert "Applied multiplier 2" in caplog.text

