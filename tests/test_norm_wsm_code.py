import pandas as pd
import wsm.ui.review.gui as rl


def test_norm_wsm_code_edges_and_booked_mask():
    values = ["0", "0,0", "", None, "<NA>", "nan", "X1", "1,5"]
    normalized = [rl._norm_wsm_code(x) for x in values]
    assert normalized[:6] == ["OSTALO"] * 6
    assert normalized[6] == "X1"
    assert normalized[7] == "1.5"

    s = pd.Series(values)
    mask = rl._booked_mask_from(s)
    assert mask.tolist() == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]


def test_booked_mask_excluded_codes_case_insensitive():
    s = pd.Series(["other", "unknown"])
    assert rl._booked_mask_from(s).tolist() == [False, False]


def test_booked_mask_respects_runtime_excluded(monkeypatch):
    s = pd.Series(["X1"])
    assert rl._booked_mask_from(s).tolist() == [True]
    monkeypatch.setattr(rl, "EXCLUDED_CODES", rl.EXCLUDED_CODES | {"X1"})
    assert rl._booked_mask_from(s).tolist() == [False]
