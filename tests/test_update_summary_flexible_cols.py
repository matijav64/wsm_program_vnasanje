import inspect
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


def test_update_summary_handles_flexible_columns(monkeypatch):
    captured: dict[str, pd.DataFrame | list] = {}

    def fake_summary_df_from_records(records):
        captured["records"] = records
        return pd.DataFrame(records)

    monkeypatch.setattr(
        summary_utils, "summary_df_from_records", fake_summary_df_from_records
    )

    _update_summary, ns = _extract_update_summary()

    df = pd.DataFrame(
        {
            "WSM šifra": ["1", "1"],
            "Skupna neto": [80, 45],
            "Bruto": [100, 50],
            "eff_discount_pct": [Decimal("20.00"), Decimal("10.00")],
        }
    )

    def flexible_first_existing(df, columns, fill_value=0):
        for col in columns:
            if col in df.columns:
                return df[col].fillna(fill_value)
            alt = col.replace("_", " ")
            if alt in df.columns:
                return df[alt].fillna(fill_value)
            alt_cap = alt.capitalize()
            if alt_cap in df.columns:
                return df[alt_cap].fillna(fill_value)
        return pd.Series(fill_value, index=df.index)

    def fake_render_summary(df_summary: pd.DataFrame) -> None:
        captured["df_summary"] = df_summary

    ns.update({
        "df": df,
        "_render_summary": fake_render_summary,
        "first_existing": flexible_first_existing,
    })
    _update_summary()

    df_summary = captured["df_summary"]
    assert len(df_summary) == 2
    net_totals = {
        (row["WSM šifra"], row["Rabat (%)"]): row["Neto po rabatu"]
        for _, row in df_summary.iterrows()
    }
    bruto_totals = {
        (row["WSM šifra"], row["Rabat (%)"]): row["Znesek"]
        for _, row in df_summary.iterrows()
    }
    assert net_totals[("1", Decimal("20.00"))] == Decimal("80")
    assert net_totals[("1", Decimal("10.00"))] == Decimal("45")
    assert bruto_totals[("1", Decimal("20.00"))] == Decimal("100")
    assert bruto_totals[("1", Decimal("10.00"))] == Decimal("50")
