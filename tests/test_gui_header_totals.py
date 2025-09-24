from decimal import Decimal
from pathlib import Path
import inspect
import textwrap
from types import SimpleNamespace

import pandas as pd
import pytest

import wsm.ui.review.gui as rl
from wsm.parsing.eslog import (
    parse_eslog_invoice,
    extract_header_net,
    extract_total_tax,
    extract_header_gross,
)
from wsm.parsing.money import detect_round_step


class DummyVar:
    def __init__(self):
        self.val = ""

    def set(self, v):
        self.val = v

    def get(self):
        return self.val


class DummyWidget:
    def __init__(self):
        self.kwargs = {}

    def config(self, **kwargs):
        self.kwargs.update(kwargs)

    def winfo_exists(self):
        return True


class DummyMessageBox:
    def showwarning(self, *args, **kwargs):
        pass


def _extract_update_totals():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, l in enumerate(src) if "def _safe_update_totals()" in l)
    indent = len(src[start]) - len(src[start].lstrip())
    end = start + 1
    while end < len(src) and (
        len(src[end]) - len(src[end].lstrip()) > indent or not src[end].strip()
    ):
        end += 1
    snippet = textwrap.dedent("\n".join(src[start:end]))
    return snippet


def test_header_totals_display_and_no_autofix(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        _r = tk.Tk()
        _r.destroy()
    except tk.TclError:
        pytest.skip("No display available")

    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>388</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "inv.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    assert ok

    links_file = tmp_path / "suppliers" / "unknown" / "x.xlsx"
    links_file.parent.mkdir(parents=True, exist_ok=True)

    wsm_df = pd.DataFrame({"wsm_naziv": ["Item"], "wsm_sifra": ["0001"]})

    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)

    def immediate_after(self, delay, func=None, *args):
        if func:
            func(*args)
        return "after"

    monkeypatch.setattr(tk.Misc, "after", immediate_after)
    monkeypatch.setattr(tk.Misc, "after_idle", lambda self, func, *a: func(*a))
    monkeypatch.setattr(tk.Misc, "after_cancel", lambda self, _id: None)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)

    res_df = rl.review_links(
        df,
        wsm_df,
        links_file,
        Decimal("10"),
        invoice_path=xml_path,
    )

    root = tk._default_root
    total_frame = next(
        c for c in root.winfo_children() if "total_sum" in getattr(c, "children", {})
    )
    total_sum = total_frame.children["total_sum"]
    assert (
        total_sum.cget("text")
        == "Neto:   10.00 €\nDDV:    2.20 €\nSkupaj: 12.20 €"
    )
    assert res_df[res_df["sifra_dobavitelja"] == "_DOC_"].empty
    root.destroy()


def test_header_shows_vat_date_and_invoice(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        _r = tk.Tk()
        _r.destroy()
    except tk.TclError:
        pytest.skip("No display available")

    tk._default_root = None

    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <S_BGM>"
        "      <C_C002><D_1001>380</D_1001></C_C002>"
        "      <C_C106><D_1004>RAC-12345</D_1004></C_C106>"
        "    </S_BGM>"
        "    <S_DTM>"
        "      <C_C507><D_2005>35</D_2005><D_2380>20250915</D_2380></C_C507>"
        "    </S_DTM>"
        "    <G_SG2>"
        "      <S_NAD>"
        "        <D_3035>SU</D_3035>"
        "        <C_C082><D_3039>SUP123</D_3039></C_C082>"
        "        <C_C080><D_3036>Supplier VAT Test</D_3036></C_C080>"
        "      </S_NAD>"
        "      <G_SG3>"
        "        <S_RFF><C_C506><D_1153>VA</D_1153><D_1154>SI73001163</D_1154></C_C506></S_RFF>"
        "      </G_SG3>"
        "    </G_SG2>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG26>"
      "  </M_INVOIC>"
      "</Invoice>"
    )
    xml_path = tmp_path / "header.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    assert ok

    links_file = tmp_path / "suppliers" / "unknown" / "x.xlsx"
    links_file.parent.mkdir(parents=True, exist_ok=True)

    wsm_df = pd.DataFrame({"wsm_naziv": ["Item"], "wsm_sifra": ["1"]})

    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)

    def immediate_after(self, delay, func=None, *args):
        if func:
            func(*args)
        return "after"

    monkeypatch.setattr(tk.Misc, "after", immediate_after)
    monkeypatch.setattr(tk.Misc, "after_idle", lambda self, func, *a: func(*a))
    monkeypatch.setattr(tk.Misc, "after_cancel", lambda self, _id: None)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self: None)

    rl.review_links(
        df,
        wsm_df,
        links_file,
        Decimal("10"),
        invoice_path=xml_path,
    )

    root = tk._default_root
    expected_title = "Ročna revizija – SI73001163"
    assert root.title() == expected_title

    header_label = next(
        widget
        for widget in root.winfo_children()
        if isinstance(widget, tk.Label) and widget.cget("textvariable")
    )
    header_var_name = header_label.cget("textvariable")
    expected_header = "SI73001163\n15.9.2025 – RAC-12345"
    assert root.getvar(header_var_name) == expected_header

    root.destroy()


def test_totals_indicator_match():
    snippet = _extract_update_totals()
    df = pd.DataFrame({"total_net": [Decimal("10.00")]})
    header_totals = {"vat": Decimal("2.00"), "gross": Decimal("12.00")}
    total_sum = DummyWidget()
    indicator = DummyWidget()
    total_frame = SimpleNamespace(children={"total_sum": total_sum})
    ns = {
        "df": df,
        "header_totals": header_totals,
        "total_frame": total_frame,
        "indicator_label": indicator,
        "Decimal": Decimal,
        "messagebox": DummyMessageBox(),
        "doc_discount": Decimal("0"),
        "neto_label": DummyWidget(),
        "ddv_label": DummyWidget(),
        "skupaj_label": DummyWidget(),
        "root": SimpleNamespace(winfo_exists=lambda: True),
        "closing": False,
        "_resolve_tolerance": rl._resolve_tolerance,
    }
    exec(snippet, ns)
    ns["_safe_update_totals"]()
    assert indicator.kwargs["text"] == "✓"
    assert indicator.kwargs["style"] == "Indicator.Green.TLabel"


def test_totals_indicator_mismatch():
    snippet = _extract_update_totals()
    df = pd.DataFrame({"total_net": [Decimal("10.00")]})
    header_totals = {"vat": Decimal("2.00"), "gross": Decimal("15.00")}
    total_sum = DummyWidget()
    indicator = DummyWidget()
    total_frame = SimpleNamespace(children={"total_sum": total_sum})
    ns = {
        "df": df,
        "header_totals": header_totals,
        "total_frame": total_frame,
        "indicator_label": indicator,
        "Decimal": Decimal,
        "messagebox": DummyMessageBox(),
        "doc_discount": Decimal("0"),
        "neto_label": DummyWidget(),
        "ddv_label": DummyWidget(),
        "skupaj_label": DummyWidget(),
        "root": SimpleNamespace(winfo_exists=lambda: True),
        "closing": False,
        "_resolve_tolerance": rl._resolve_tolerance,
    }
    exec(snippet, ns)
    ns["_safe_update_totals"]()
    assert indicator.kwargs["text"] == "✗"
    assert indicator.kwargs["style"] == "Indicator.Red.TLabel"


def test_no_doc_row_added_for_small_diff(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10.02</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10.02</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>388</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "inv.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    header_net = extract_header_net(xml_path)
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty
    total_calc = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    step = detect_round_step(header_net, total_calc)
    diff = header_net - total_calc
    assert abs(diff) <= step and diff != 0
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty


def test_header_totals_display_small_diff(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>0001</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_PRI><C_C509><D_5125>AAA</D_5125><D_5118>10.02</D_5118></C_C509></S_PRI>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10.02</D_5004></C_C516></S_MOA>"
        "      <G_SG34><S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX></G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>388</D_5025><D_5004>12.20</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>2.20</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "inv.xml"
    xml_path.write_text(xml)

    df, ok = parse_eslog_invoice(xml_path)
    header = {
        "net": extract_header_net(xml_path),
        "vat": extract_total_tax(xml_path),
        "gross": extract_header_gross(xml_path),
    }
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty
    total_calc = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    step = detect_round_step(header["net"], total_calc)
    diff = header["net"] - total_calc
    assert abs(diff) <= step and diff != 0

    snippet = _extract_update_totals()
    total_sum = DummyWidget()
    indicator = DummyWidget()
    total_frame = SimpleNamespace(children={"total_sum": total_sum})
    ns = {
        "df": pd.DataFrame({"total_net": [total_calc]}),
        "header_totals": header,
        "total_frame": total_frame,
        "indicator_label": indicator,
        "Decimal": Decimal,
        "messagebox": DummyMessageBox(),
        "doc_discount": Decimal("0"),
        "root": SimpleNamespace(winfo_exists=lambda: True),
        "closing": False,
        "_resolve_tolerance": rl._resolve_tolerance,
    }
    exec(snippet, ns)
    ns["_safe_update_totals"]()
    assert total_sum.kwargs["text"] == (
        f"Neto:   {total_calc:,.2f} €\nDDV:    {header['vat']:,.2f} €\nSkupaj: {(total_calc + header['vat']):,.2f} €"
    )
