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
        "doc_discount": Decimal("0"),
    }
    ns["_build_wsm_summary"] = _stub_build_summary(ns)
    exec(snippet, ns)
    return ns["_update_summary"], ns


def _stub_build_summary(ns):
    def _builder(df_all: pd.DataFrame, hdr_net_total: Decimal | None):
        doc_disc = ns.get("doc_discount", Decimal("0"))
        qty_total = sum(Decimal(str(v)) for v in df_all.get("kolicina_norm", []))
        net_raw = sum(Decimal(str(v)) for v in df_all.get("vrednost", []))
        net_discounted = sum(
            Decimal(str(v)) for v in df_all.get("Skupna neto", df_all.get("total_net", []))
        )
        records = [
            {
                "WSM šifra": "",
                "WSM Naziv": "",
                "Količina": qty_total,
                "Znesek": net_raw,
                "Rabat (%)": Decimal("0"),
                "Neto po rabatu": net_discounted,
            }
        ]
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
        net_diff = None
        if hdr_net_total is not None:
            net_diff = hdr_net_total - (net_discounted + doc_disc)
        return summary_utils.summary_df_from_records(records), "", net_diff

    return _builder


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
    rec = records[0]
    assert rec["Znesek"] == Decimal("150")
    assert rec["Neto po rabatu"] == Decimal("125")
    assert rec["Količina"] == Decimal("2")


def test_update_summary_records_return_value(monkeypatch):
    records_holder: dict[str, list] = {}

    def fake_summary_df_from_records(records):
        records_holder["records"] = records
        return pd.DataFrame()

    monkeypatch.setattr(
        summary_utils, "summary_df_from_records", fake_summary_df_from_records
    )

    _update_summary, ns = _extract_update_summary()
    ns["doc_discount"] = Decimal("-10")
    df = pd.DataFrame(
        {
            "wsm_sifra": ["1", "1"],
            "wsm_naziv": ["Item", "Item"],
            "kolicina_norm": [Decimal("1"), Decimal("-1")],
            "Skupna neto": [Decimal("80"), Decimal("-80")],
            "eff_discount_pct": [Decimal("0"), Decimal("0")],
        }
    )

    ns.update({"df": df, "_render_summary": lambda df: None})

    _update_summary()

    records = records_holder["records"]
    assert len(records) == 2
    doc_row = next(r for r in records if r["WSM Naziv"] == "DOKUMENTARNI POPUST")
    agg_row = next(r for r in records if r.get("WSM Naziv") == "")
    assert doc_row["Neto po rabatu"] == Decimal("-10")
    assert doc_row["Količina"] == Decimal("0")
    # glavna vrstica sešteva samo postavke (80 + -80 = 0)
    assert agg_row["Neto po rabatu"] == Decimal("0")
