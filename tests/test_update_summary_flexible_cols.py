import inspect
import textwrap
from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.ui.review.helpers import first_existing
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
        "compute_eff_discount_pct_robust":
            lambda df, *a, **k: pd.Series([Decimal("0.00")] * len(df)),
        "log": rl.log,
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

    def fake_compute_eff_discount_pct(df, *a, **k):
        res = []
        for bruto, net in zip(df["Bruto"], df["Skupna neto"]):
            b = Decimal(str(bruto))
            n = Decimal(str(net))
            pct = ((b - n) / b * Decimal("100")).quantize(Decimal("0.01"))
            res.append(pct)
        return pd.Series(res)

    ns.update({
        "df": df,
        "_render_summary": fake_render_summary,
        "first_existing": flexible_first_existing,
    })
    ns["compute_eff_discount_pct_robust"] = fake_compute_eff_discount_pct

    _update_summary()

    df_summary = captured["df_summary"]
    assert len(df_summary) == 2
    totals = {
        (row["WSM šifra"], row["Rabat (%)"]): row["Znesek"]
        for _, row in df_summary.iterrows()
    }
    assert totals[("1", Decimal("20.00"))] == Decimal("80")
    assert totals[("1", Decimal("10.00"))] == Decimal("45")
    for _, row in df_summary.iterrows():
        assert row["Neto po rabatu"] == row["Znesek"]
