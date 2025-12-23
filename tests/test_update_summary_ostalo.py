import inspect
import textwrap
from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.ui.review.helpers import first_existing, first_existing_series
import wsm.ui.review.summary_utils as summary_utils


def _stub_build_summary(ns):
    def _builder(df_all: pd.DataFrame, hdr_net_total: Decimal | None):
        doc_disc = ns.get("doc_discount", Decimal("0"))

        code_s = first_existing_series(
            df_all, ["_summary_key", "_booked_sifra", "wsm_sifra", "WSM šifra"]
        )
        if code_s is None:
            code_s = pd.Series([""] * len(df_all), index=df_all.index)
        code_s = code_s.astype("string").fillna("").str.strip()

        excl_fn = ns.get("_excluded_codes_upper")
        excluded = excl_fn() if callable(excl_fn) else frozenset()
        code_upper = code_s.str.upper()
        is_booked = code_s.ne("") & ~code_upper.isin(excluded)
        eff_code = code_s.where(is_booked, "OSTALO")

        qty_s = first_existing_series(
            df_all, ["kolicina_norm", "Količina"], fill_value=Decimal("0")
        )
        amount_raw_s = first_existing_series(
            df_all,
            ["total_raw", "Bruto", "vrednost", "total_net", "Skupna neto"],
            fill_value=Decimal("0"),
        )
        amount_discounted_s = first_existing_series(
            df_all,
            ["total_net", "Neto po rabatu", "Skupna neto", "vrednost"],
            fill_value=Decimal("0"),
        )
        if "cena_pred_rabatom" in df_all.columns and (
            amount_raw_s is None or amount_raw_s.fillna(Decimal("0")).eq(0).all()
        ):
            try:
                unit_price = df_all["cena_pred_rabatom"].map(lambda v: Decimal(str(v)))
                amount_raw_s = qty_s * unit_price
            except Exception:
                pass
        rab_s = first_existing_series(
            df_all, ["rabata_pct", "eff_discount_pct"], fill_value=Decimal("0")
        )
        name_s = first_existing_series(
            df_all, ["WSM Naziv", "WSM naziv", "wsm_naziv"]
        )
        if name_s is None:
            name_s = pd.Series([""] * len(df_all), index=df_all.index, dtype="string")
        name_s = name_s.astype("string").fillna("")

        work = pd.DataFrame(
            {
                "code": eff_code,
                "qty": qty_s,
                "net_raw": amount_raw_s,
                "net_discounted": amount_discounted_s,
                "rabat": rab_s,
                "name": name_s,
            }
        )

        def _norm_rabat(val):
            try:
                dec_val = val if isinstance(val, Decimal) else Decimal(str(val))
            except Exception:
                dec_val = Decimal("0")
            try:
                dec_val = dec_val.quantize(Decimal("0.00"))
            except Exception:
                pass
            try:
                if abs(dec_val) < ns.get("DEC_SMALL_DISCOUNT", Decimal("0")):
                    dec_val = Decimal("0.00")
            except Exception:
                pass
            return dec_val

        work["rabat"] = work["rabat"].map(_norm_rabat)

        def _dsum(series):
            total = Decimal("0")
            for v in series:
                if v in (None, ""):
                    continue
                try:
                    total += v if isinstance(v, Decimal) else Decimal(str(v))
                except Exception:
                    continue
            return total

        records: list[dict[str, object]] = []
        for (code, rab), g in work.groupby(["code", "rabat"], dropna=False):
            net_total_raw = _dsum(g["net_raw"])
            net_total_discounted = _dsum(g["net_discounted"])
            qty_total = _dsum(g["qty"])

            rab_val = rab

            if code == "OSTALO":
                disp_code = "OSTALO"
                disp_name = "Ostalo"
                rab_val = Decimal("0.00")
            else:
                disp_code = str(code)
                nm = g["name"].astype(str).str.strip()
                nm = nm[nm != ""]
                disp_name = nm.iloc[0] if len(nm) else disp_code

            records.append(
                {
                    "WSM šifra": disp_code,
                    "WSM Naziv": disp_name,
                    "Količina": qty_total,
                    "Znesek": net_total_raw,
                    "Rabat (%)": rab_val,
                    "Neto po rabatu": net_total_discounted,
                }
            )

        if doc_disc != 0:
            records.append(
                {
                    "WSM šifra": "",
                    "WSM Naziv": "DOKUMENTARNI POPUST",
                    "Količina": Decimal("0"),
                    "Znesek": doc_disc,
                    "Rabat (%)": Decimal("0"),
                    "Neto po rabatu": doc_disc,
                }
            )

        merged: dict[tuple[object, object, object], dict[str, object]] = {}
        for rec in records:
            key = (
                rec.get("WSM šifra"),
                rec.get("Rabat (%)"),
                rec.get("WSM Naziv"),
            )
            if key in merged:
                try:
                    merged[key]["Količina"] += rec.get("Količina", Decimal("0"))
                    merged[key]["Znesek"] += rec.get("Znesek", Decimal("0"))
                    merged[key]["Neto po rabatu"] += rec.get(
                        "Neto po rabatu", Decimal("0")
                    )
                except Exception:
                    pass
            else:
                merged[key] = rec
        records = list(merged.values())

        summary_df = summary_utils.summary_df_from_records(records)
        grid_net_total = _dsum(work["net_discounted"]) + doc_disc
        net_diff = (
            None
            if hdr_net_total is None
            else Decimal(str(hdr_net_total)) - grid_net_total
        )
        status = "" if hdr_net_total is None else ("Δ" if net_diff != 0 else "")
        return summary_df, status, net_diff

    return _builder


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
        "doc_discount": Decimal("0"),
        "net_icon_label_holder": {"widget": None},
    }
    ns["_build_wsm_summary"] = _stub_build_summary(ns)
    exec(snippet, ns)
    orig_update = ns["_update_summary"]

    def _update_summary_wrapper(*args, **kwargs):
        df = ns.get("df")
        if isinstance(df, pd.DataFrame):
            df = df.copy()
            if "_booked_sifra" in df.columns:
                booked = df["_booked_sifra"].astype("string").fillna("")
                ostalo_mask = booked.str.upper().eq("OSTALO")
                if "_summary_key" not in df.columns:
                    df["_summary_key"] = ""
                df["_summary_key"] = pd.Series(df["_summary_key"], copy=True)
                df.loc[ostalo_mask, "_summary_key"] = "OSTALO"
                df["_booked_sifra"] = booked.where(~ostalo_mask, "")
            ns["df"] = df
        return orig_update(*args, **kwargs)

    ns["_update_summary"] = _update_summary_wrapper
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
