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
    }
    exec(snippet, ns)
    return ns["_update_summary"], ns


def test_update_summary_uses_discounted_net(monkeypatch):
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
            "vrednost": [100, 50],
            "rabata": [20, 5],
            "kolicina_norm": [1, 1],
            "Skupna neto": [80, 45],
            "eff_discount_pct": [Decimal("0"), Decimal("0")],
        }
    )
    ns.update({"df": df, "_render_summary": lambda df: None})

    _update_summary()

    records = records_holder["records"]
    assert len(records) == 1
    assert records[0]["Znesek"] == Decimal("150")
    assert records[0]["Neto po rabatu"] == Decimal("125")
