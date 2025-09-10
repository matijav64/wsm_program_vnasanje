from wsm.ui.review.helpers import _norm_wsm_code

def test_norm_wsm_code_basic():
    assert _norm_wsm_code(None) == ""
    assert _norm_wsm_code("") == ""
    assert _norm_wsm_code(" 100100 ") == "100100"
    assert _norm_wsm_code("100100.0") == "100100"
    assert _norm_wsm_code("00123") == "00123"

