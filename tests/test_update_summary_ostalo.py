import inspect
import textwrap
from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.ui.review.helpers import first_existing, first_existing_series
import wsm.ui.review.summary_utils as summary_utils


def _extract_update_summary():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(
        i for i, line in enumerate(src)
        if "def _fallback_count_from_grid" in line
    )
    end = next(
        i
        for i, line in enumerate(src[start:], start)
        if line.startswith("    # Skupni zneski")
    )
    snippet = textwrap.dedent("\n".join(src[start:end]))
    ns = {
        "pd": pd,
        "Decimal": Decimal,
        "first_existing": first_existing,
        "log": rl.log,
        "first_existing_series": first_existing_series,
        "summary_df_from_records": summary_utils.summary_df_from_records,
        "ONLY_BOOKED_IN_SUMMARY": rl.ONLY_BOOKED_IN_SUMMARY,
        "EXCLUDED_CODES": rl.EXCLUDED_CODES,
        "_excluded_codes_upper": rl._excluded_codes_upper,
        "_booked_mask_from": rl._booked_mask_from,
        "_norm_wsm_code": rl._norm_wsm_code,
        "wsm_df": pd.DataFrame(),
        "DEC_SMALL_DISCOUNT": rl.DEC_SMALL_DISCOUNT,
    }
    exec(snippet, ns)
    return ns["_update_summary"], ns


def test_update_summary_preserves_discount_for_unbooked():
    _update_summary, ns = _extract_update_summary()

    captured = {}

    def fake_render_summary(df_summary: pd.DataFrame) -> None:
        captured["df_summary"] = df_summary

    df = pd.DataFrame(
        {
            "WSM šifra": [""],
            "wsm_naziv": ["Some item"],
            "Neto po rabatu": [Decimal("80")],
            "kolicina_norm": [Decimal("1")],
            "eff_discount_pct": [Decimal("20")],
        }
    )

    ns.update({"df": df, "_render_summary": fake_render_summary})
    _update_summary()

    df_summary = captured["df_summary"]
    assert "Rabat (%)" in df_summary.columns
    assert df_summary.loc[0, "Rabat (%)"] == Decimal("0.00")
    assert df_summary.loc[0, "WSM Naziv"] == "Ostalo"
    assert df_summary.loc[0, "WSM šifra"] == "OSTALO"


def test_update_summary_mixed_booked_unbooked():
    _update_summary, ns = _extract_update_summary()

    captured: dict[str, pd.DataFrame] = {}

    def fake_render_summary(df_summary: pd.DataFrame) -> None:
        captured["df_summary"] = df_summary

    df = pd.DataFrame(
        {
            "WSM šifra": ["123", ""],
            "wsm_naziv": ["BANANE", "NEKI"],
            "Neto po rabatu": [Decimal("100"), Decimal("80")],
            "kolicina_norm": [Decimal("1"), Decimal("1")],
            "eff_discount_pct": [Decimal("15.00"), Decimal("20.00")],
        }
    )

    ns.update({"df": df, "_render_summary": fake_render_summary})
    _update_summary()

    out = captured["df_summary"]
    assert any(
        (out["WSM šifra"] == "123") & (out["Rabat (%)"] == Decimal("15.00"))
    )
    assert any(
        (out["WSM Naziv"] == "Ostalo") & (out["Rabat (%)"] == Decimal("0.00"))
    )


def test_update_summary_keeps_ostalo_when_not_booked():
    _update_summary, ns = _extract_update_summary()

    captured: dict[str, pd.DataFrame] = {}

    def fake_render_summary(df_summary: pd.DataFrame) -> None:
        captured["df_summary"] = df_summary

    df = pd.DataFrame(
        {
            "_booked_sifra": ["OSTALO"],
            "wsm_sifra": ["123456"],
            "wsm_naziv": ["Predlog"],
            "Neto po rabatu": [Decimal("10")],
            "kolicina_norm": [Decimal("1")],
            "eff_discount_pct": [Decimal("0")],
        }
    )

    ns.update({"df": df, "_render_summary": fake_render_summary})
    _update_summary()

    df_summary = captured["df_summary"]
    assert list(df_summary["WSM šifra"]) == ["OSTALO"]
    assert list(df_summary["WSM Naziv"]) == ["Ostalo"]
    assert ns.get("_SUMMARY_COUNTS") == (0, 1)


def test_update_summary_reflects_confirmed_booking():
    _update_summary, ns = _extract_update_summary()

    captured: dict[str, pd.DataFrame] = {}

    def fake_render_summary(df_summary: pd.DataFrame) -> None:
        captured["df_summary"] = df_summary

    df = pd.DataFrame(
        {
            "_booked_sifra": ["123"],
            "_summary_key": ["123"],
            "WSM šifra": ["123"],
            "wsm_naziv": ["BANANE"],
            "Neto po rabatu": [Decimal("10")],
            "kolicina_norm": [Decimal("1")],
            "eff_discount_pct": [Decimal("0")],
        }
    )

    ns.update({"df": df, "_render_summary": fake_render_summary})
    _update_summary()

    df_summary = captured["df_summary"]
    assert list(df_summary["WSM šifra"]) == ["123"]
    assert ns.get("_SUMMARY_COUNTS") == (1, 0)


def test_coerce_booked_code_handles_excluded_and_empty():
    assert rl._coerce_booked_code(" 123.0 ") == "123"
    assert rl._coerce_booked_code(None) == "OSTALO"
    assert rl._coerce_booked_code("0") == "OSTALO"
    assert rl._coerce_booked_code("ostalo") == "OSTALO"

