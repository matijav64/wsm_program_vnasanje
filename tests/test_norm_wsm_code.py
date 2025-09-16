from wsm.ui.review.helpers import _norm_wsm_code


def test_norm_wsm_code_basic():
    assert _norm_wsm_code(None) == ""
    assert _norm_wsm_code("") == ""
    assert _norm_wsm_code(" 100100 ") == "100100"
    assert _norm_wsm_code("100100.0") == "100100"
    assert _norm_wsm_code("00123") == "00123"
    assert _norm_wsm_code("0") == ""
    assert _norm_wsm_code("0.0") == ""
    assert _norm_wsm_code("0,0") == ""
    assert _norm_wsm_code("000") == ""
    assert _norm_wsm_code("nan") == ""
    assert _norm_wsm_code("NaN") == ""
    assert _norm_wsm_code("NONE") == ""
    assert _norm_wsm_code("null") == ""
