from decimal import Decimal
from pathlib import Path
import inspect
import textwrap
from types import SimpleNamespace

import pandas as pd

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


class DummyMessageBox:
    def showwarning(self, *args, **kwargs):
        pass


def _extract_update_totals():
    src = inspect.getsource(rl.review_links).splitlines()
    start = next(i for i, l in enumerate(src) if "def _update_totals()" in l)
    indent = len(src[start]) - len(src[start].lstrip())
    end = start + 1
    while end < len(src) and (
        len(src[end]) - len(src[end].lstrip()) > indent or not src[end].strip()
    ):
        end += 1
    snippet = textwrap.dedent("\n".join(src[start:end]))
    return snippet


def test_header_totals_display_and_no_autofix(tmp_path):
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
    header = {
        "net": extract_header_net(xml_path),
        "vat": extract_total_tax(xml_path),
        "gross": extract_header_gross(xml_path),
    }
    assert df[df["sifra_dobavitelja"] == "_DOC_"].empty
    total_calc = df[df["sifra_dobavitelja"] != "_DOC_"]["vrednost"].sum()
    step = detect_round_step(header["net"], total_calc)
    diff = header["net"] - total_calc
    assert not (abs(diff) <= step and diff != 0)

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
    }
    exec(snippet, ns)
    ns["_update_totals"]()
    assert total_sum.kwargs["text"] == (
        "Neto:   10.00 €\nDDV:    2.20 €\nSkupaj: 12.20 €"
    )


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
    }
    exec(snippet, ns)
    ns["_update_totals"]()
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
    }
    exec(snippet, ns)
    ns["_update_totals"]()
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
    }
    exec(snippet, ns)
    ns["_update_totals"]()
    assert total_sum.kwargs["text"] == (
        f"Neto:   {total_calc:,.2f} €\nDDV:    {header['vat']:,.2f} €\nSkupaj: {(total_calc + header['vat']):,.2f} €"
    )
