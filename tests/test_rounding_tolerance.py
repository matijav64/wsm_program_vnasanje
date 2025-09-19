from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as gui


def _make_df(amount: Decimal) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sifra_dobavitelja": "ITEM",
                "naziv": "Test",
                "naziv_ckey": "test",
                "kolicina": Decimal("1"),
                "enota": "kos",
                "vrednost": amount,
                "rabata": Decimal("0"),
                "ddv": Decimal("0"),
                "ddv_stopnja": Decimal("0"),
                "wsm_sifra": "OSTALO",
                "wsm_naziv": "Test",
                "WSM šifra": "OSTALO",
                "WSM Naziv": "Test",
                "status": "",
                "multiplier": Decimal("1"),
            }
        ]
    )


def test_rounding_correction_adds_row_when_enabled(monkeypatch):
    monkeypatch.setattr(gui, "ROUNDING_CORRECTION_ENABLED", True)
    monkeypatch.setattr(gui, "SMART_TOLERANCE_ENABLED", True)
    monkeypatch.setattr(gui, "DEFAULT_TOLERANCE", Decimal("0.02"))
    monkeypatch.setattr(gui, "TOLERANCE_BASE", Decimal("0.02"))
    monkeypatch.setattr(gui, "MAX_TOLERANCE", Decimal("0.50"))
    header = {"net": Decimal("10.00"), "gross": Decimal("10.00")}
    df = _make_df(Decimal("10.04"))
    expected_tolerance = gui._resolve_tolerance(Decimal("10.04"), Decimal("10.00"))

    corrected = gui._maybe_apply_rounding_correction(df, header, Decimal("0"))

    assert (corrected["sifra_dobavitelja"] == "_ROUND_").any()
    row = corrected[corrected["sifra_dobavitelja"] == "_ROUND_"].iloc[0]
    assert row["vrednost"] == Decimal("-0.04")
    assert row.get("status") == "AUTO_CORRECTION"
    assert expected_tolerance == Decimal("0.02")


def test_rounding_correction_disabled(monkeypatch):
    monkeypatch.setattr(gui, "ROUNDING_CORRECTION_ENABLED", False)
    header = {"net": Decimal("10.00"), "gross": Decimal("10.00")}
    df = _make_df(Decimal("10.01"))

    corrected = gui._maybe_apply_rounding_correction(df, header, Decimal("0"))

    assert not (corrected["sifra_dobavitelja"] == "_ROUND_").any()


def test_rounding_correction_skips_when_within_tolerance(monkeypatch):
    monkeypatch.setattr(gui, "ROUNDING_CORRECTION_ENABLED", True)
    monkeypatch.setattr(gui, "SMART_TOLERANCE_ENABLED", True)
    monkeypatch.setattr(gui, "DEFAULT_TOLERANCE", Decimal("0.02"))
    monkeypatch.setattr(gui, "TOLERANCE_BASE", Decimal("0.05"))
    monkeypatch.setattr(gui, "MAX_TOLERANCE", Decimal("0.50"))
    header = {"net": Decimal("10.00"), "gross": Decimal("10.00")}
    df = _make_df(Decimal("10.03"))

    tolerance = gui._resolve_tolerance(Decimal("10.03"), Decimal("10.00"))
    assert tolerance == Decimal("0.05")

    corrected = gui._maybe_apply_rounding_correction(df, header, Decimal("0"))

    assert not (corrected["sifra_dobavitelja"] == "_ROUND_").any()


def test_large_invoice_tolerance(monkeypatch):
    monkeypatch.setattr(gui, "SMART_TOLERANCE_ENABLED", True)
    monkeypatch.setattr(gui, "DEFAULT_TOLERANCE", Decimal("0.02"))
    monkeypatch.setattr(gui, "TOLERANCE_BASE", Decimal("0.02"))
    monkeypatch.setattr(gui, "MAX_TOLERANCE", Decimal("0.50"))

    tolerance = gui._resolve_tolerance(Decimal("15000.00"), Decimal("15000.00"))

    assert tolerance == Decimal("0.50")


def test_rounding_correction_does_not_duplicate_existing_row(monkeypatch):
    monkeypatch.setattr(gui, "ROUNDING_CORRECTION_ENABLED", True)
    monkeypatch.setattr(gui, "SMART_TOLERANCE_ENABLED", True)
    monkeypatch.setattr(gui, "DEFAULT_TOLERANCE", Decimal("0.02"))
    monkeypatch.setattr(gui, "TOLERANCE_BASE", Decimal("0.02"))
    monkeypatch.setattr(gui, "MAX_TOLERANCE", Decimal("0.50"))

    header = {"net": Decimal("10.00"), "gross": Decimal("10.00")}
    df = _make_df(Decimal("10.20"))
    rounding_row = {
        "sifra_dobavitelja": "_ROUND_",
        "naziv": "Zaokrožitev",
        "naziv_ckey": "zaokrožitev",
        "kolicina": Decimal("1"),
        "enota": "kos",
        "vrednost": Decimal("-0.04"),
        "rabata": Decimal("0"),
        "ddv": Decimal("0"),
        "ddv_stopnja": Decimal("0"),
        "wsm_sifra": "OSTALO",
        "wsm_naziv": "Zaokrožitev",
        "WSM šifra": "OSTALO",
        "WSM Naziv": "Zaokrožitev",
        "status": "AUTO_CORRECTION",
        "multiplier": Decimal("1"),
    }
    df = pd.concat([df, pd.DataFrame([rounding_row])], ignore_index=True)

    corrected = gui._maybe_apply_rounding_correction(df, header, Decimal("0"))

    assert (corrected["sifra_dobavitelja"] == "_ROUND_").sum() == 1
