import logging
import inspect
import textwrap
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

import wsm.ui.review.gui as rl
from wsm.parsing.money import detect_round_step
from wsm.ui.review.helpers import _split_totals


class DummyLabel:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]

    def winfo_exists(self):
        return True


class DummyMsgBox:
    @staticmethod
    def showwarning(*args, **kwargs):
        pass


class DummyFrame:
    def __init__(self, child):
        self.children = {"total_sum": child}


def _extract_update_func():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, l in enumerate(src) if "def _safe_update_totals()" in l)
    indent = len(src[start]) - len(src[start].lstrip())
    end = start + 1
    while end < len(src) and (
        len(src[end]) - len(src[end].lstrip()) > indent or not src[end].strip()
    ):
        end += 1
    return textwrap.dedent("\n".join(src[start:end]))


def test_totals_label_contains_terms():
    snippet = _extract_update_func()
    lbl = DummyLabel()
    indicator = DummyLabel()
    df = pd.DataFrame(
        {
            "total_net": [Decimal("10")],
            "ddv": [Decimal("2")],
            "wsm_sifra": ["A"],
        }
    )
    df_doc = pd.DataFrame()
    ns = {
        "Decimal": Decimal,
        "df": df,
        "df_doc": df_doc,
        "doc_discount_total": Decimal("0"),
        "doc_discount": Decimal("0"),
        "header_totals": {
            "net": Decimal("10"),
            "vat": Decimal("2"),
            "gross": Decimal("12"),
        },
        "detect_round_step": detect_round_step,
        "_resolve_tolerance": rl._resolve_tolerance,
        "_split_totals": _split_totals,
        "messagebox": DummyMsgBox,
        "total_frame": DummyFrame(lbl),
        "indicator_label": indicator,
        "neto_label": DummyLabel(),
        "ddv_label": DummyLabel(),
        "skupaj_label": DummyLabel(),
        "log": logging.getLogger("test"),
        "root": SimpleNamespace(winfo_exists=lambda: True),
        "closing": False,
    }
    exec(snippet, ns)
    ns["_safe_update_totals"]()
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
