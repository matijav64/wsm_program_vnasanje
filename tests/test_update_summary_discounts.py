import inspect
import textwrap
from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.ui.review.helpers import first_existing, first_existing_series
import wsm.ui.review.summary_utils as summary_utils


def _extract_update_summary():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, line in enumerate(src) if "def _update_summary" in line)
    end = next(
        i for i, line in enumerate(src[start:], start)
        if line.startswith("    # Skupni zneski")
    )
    snippet = textwrap.dedent("\n".join(src[start:end]))
    ns = {
        "pd": pd,
        "Decimal": Decimal,
        "first_existing": first_existing,
        "log": rl.log,
        "first_existing_series": first_existing_series,
        "series_to_dec": lambda s: s.map(Decimal),
        "to_dec": Decimal,
        "summary_df_from_records": summary_utils.summary_df_from_records,
        "np": __import__("numpy"),
        "_excluded_codes_upper": rl._excluded_codes_upper,
        "_booked_mask_from": rl._booked_mask_from,
        "_norm_wsm_code": rl._norm_wsm_code,
        "DEC_SMALL_DISCOUNT": rl.DEC_SMALL_DISCOUNT,

    }
    exec(snippet, ns)
    return ns["_update_summary"], ns


def test_update_summary_splits_per_discount(monkeypatch):
    records_holder: dict[str, list] = {}

    def fake_summary_df_from_records(records):
        records_holder["records"] = records
        return pd.DataFrame()

    monkeypatch.setattr(
        summary_utils, "summary_df_from_records", fake_summary_df_from_records
    )

    _update_summary, ns = _extract_update_summary()

    df = pd.DataFrame(
        {
            "wsm_sifra": ["1", "1"],
            "wsm_naziv": ["Item", "Item"],
            "vrednost": [100, 100],
            "rabata": [20, 10],
            "kolicina_norm": [1, 1],
            "Skupna neto": [80, 90],
            "eff_discount_pct": [Decimal("20"), Decimal("10")],
        }
    )
    ns.update({"df": df, "_render_summary": lambda df: None})

    _update_summary()

    records = records_holder["records"]
    assert len(records) == 2
    discounts = {record["Rabat (%)"] for record in records}
    assert discounts == {Decimal("20"), Decimal("10")}


def test_update_summary_merges_rounding_discounts(monkeypatch):
    records_holder: dict[str, list] = {}

    def fake_summary_df_from_records(records):
        records_holder["records"] = records
        return pd.DataFrame()

    monkeypatch.setattr(
        summary_utils, "summary_df_from_records", fake_summary_df_from_records
    )

    _update_summary, ns = _extract_update_summary()

    df = pd.DataFrame(
        {
            "wsm_sifra": ["123", "123"],
            "WSM šifra": ["123", "123"],
            "wsm_naziv": ["COCA COLA", "COCA COLA"],
            "naziv_ckey": ["coca cola", "coca cola"],
            "enota_norm": ["kos", "kos"],
            "kolicina_norm": [Decimal("24"), Decimal("24")],
            "Skupna neto": [Decimal("17.76"), Decimal("35.52")],
            "cena_po_rabatu": [Decimal("0.74"), Decimal("0.74")],
            "eff_discount_pct": [Decimal("-0.05"), Decimal("0.00")],
            "rabata_pct": [Decimal("-0.05"), Decimal("0.00")],
        }
    )

    ns.update({"df": df, "_render_summary": lambda df: None})

    _update_summary()

    records = records_holder["records"]
    assert len(records) == 1
    assert records[0]["Rabat (%)"] == Decimal("0.00")
