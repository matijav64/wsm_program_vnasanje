import inspect
import textwrap
from decimal import Decimal

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.parsing.money import detect_round_step
from wsm.ui.review.helpers import _split_totals


class DummyLabel:
    def __init__(self):
        self.text = ""

    def config(self, *, text):
        self.text = text


class DummyMsgBox:
    @staticmethod
    def showwarning(*args, **kwargs):
        pass


class DummyFrame:
    def __init__(self, child):
        self.children = {"total_sum": child}


def _extract_update_func():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, l in enumerate(src) if "def _update_totals" in l)
    end = next(
        i
        for i in range(start + 1, len(src))
        if src[i].lstrip().startswith("bottom =")
    )
    return textwrap.dedent("\n".join(src[start:end]))


def test_totals_label_contains_terms():
    snippet = _extract_update_func()
    lbl = DummyLabel()
    df = pd.DataFrame(
        {"total_net": [Decimal("10")], "ddv": [Decimal("2")], "wsm_sifra": ["A"]}
    )
    df_doc = pd.DataFrame()
    ns = {
        "Decimal": Decimal,
        "df": df,
        "df_doc": df_doc,
        "doc_discount_total": Decimal("0"),
        "header_totals": {
            "net": Decimal("10"),
            "vat": Decimal("2"),
            "gross": Decimal("12"),
        },
        "detect_round_step": detect_round_step,
        "_split_totals": _split_totals,
        "messagebox": DummyMsgBox,
        "total_frame": DummyFrame(lbl),
    }
    exec(snippet, ns)
    ns["_update_totals"]()
    assert "DDV:" in lbl.text
    assert "Skupaj:" in lbl.text


def test_split_totals_simple():
    df = pd.DataFrame({"wsm_sifra": ["X"], "total_net": [Decimal("100")]})
    result = _split_totals(df, Decimal("0"), vat_rate=Decimal("0.095"))
    assert result == (
        Decimal("100"),
        Decimal("9.5"),
        Decimal("109.5"),
    )
