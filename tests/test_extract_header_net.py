from decimal import Decimal
from pathlib import Path

from lxml import etree as LET

from wsm.parsing.eslog import (
    extract_header_net,
    parse_eslog_invoice,
    parse_invoice_totals,
)



def test_extract_header_net_falls_back_to_moa_79(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>45.67</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    xml_path = tmp_path / "moa79.xml"
    xml_path.write_text(xml)
    assert extract_header_net(xml_path) == Decimal("45.67")


def test_extract_header_net_handles_doc_discount(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-5</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "disc.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("95.00")


def test_extract_header_net_handles_doc_charge(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100</D_5004></C_C516></S_MOA>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>504</D_5025><D_5004>5</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "charge.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("105.00")


def test_extract_header_net_prefers_best_header_match(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>2</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>100.02</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "moa_mismatch.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("100.02")


def test_extract_header_net_prefers_gross_match_when_available(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_LIN><C_C212><D_7140>2</D_7140></C_C212></S_LIN>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>100.02</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>109.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>9.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "moa_with_gross.xml"
    path.write_text(xml)
    assert extract_header_net(path) == Decimal("100.00")


def test_parse_eslog_invoice_trusts_consistent_header_totals(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><D_1082>1</D_1082></S_LIN>"
        "      <S_QTY><C_C186><D_6063>47</D_6063><D_6060>1.00</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "      <G_SG34>"
        "        <S_TAX><C_C241><D_5153>VAT</D_5153></C_C241></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>4.50</D_5004></C_C516></S_MOA>"
        "        <S_MOA><C_C516><D_5025>125</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_LIN><D_1082>2</D_1082></S_LIN>"
        "      <S_QTY><C_C186><D_6063>47</D_6063><D_6060>1.00</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "      <G_SG34>"
        "        <S_TAX><C_C241><D_5153>VAT</D_5153></C_C241></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>4.50</D_5004></C_C516></S_MOA>"
        "        <S_MOA><C_C516><D_5025>125</D_5025><D_5004>50.01</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>79</D_5025><D_5004>100.02</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>109.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>9.00</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "header_totals.xml"
    path.write_text(xml)

    df, ok = parse_eslog_invoice(path)

    assert ok
    assert not df.attrs.get("gross_mismatch", False)
    assert df.attrs["gross_calc"] == Decimal("109.00")


def test_parse_eslog_invoice_prefers_header_gross_without_header_vat(tmp_path):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><D_1082>1</D_1082></S_LIN>"
        "      <S_QTY><C_C186><D_6063>47</D_6063><D_6060>1.00</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.00</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "      <G_SG34>"
        "        <S_TAX><C_C241><D_5153>VAT</D_5153></C_C241><C_C243><D_5278>22.00</D_5278></C_C243><D_5305>S</D_5305></S_TAX>"
        "        <TaxAmount>11.03</TaxAmount>"
        "        <S_MOA><C_C516><D_5025>125</D_5025><D_5004>50.00</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG26>"
        "      <S_LIN><D_1082>2</D_1082></S_LIN>"
        "      <S_QTY><C_C186><D_6063>47</D_6063><D_6060>1.00</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>50.00</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "      <G_SG34>"
        "        <S_TAX><C_C241><D_5153>VAT</D_5153></C_C241><C_C243><D_5278>22.00</D_5278></C_C243><D_5305>S</D_5305></S_TAX>"
        "        <TaxAmount>11.03</TaxAmount>"
        "        <S_MOA><C_C516><D_5025>125</D_5025><D_5004>50.00</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>122.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path = tmp_path / "header_totals_no_vat.xml"
    path.write_text(xml)

    df, ok = parse_eslog_invoice(path)

    assert df["ddv"].sum() == Decimal("22.06")
    assert ok
    assert not df.attrs.get("gross_mismatch", False)
    assert df.attrs["gross_calc"] == Decimal("122.00")

    totals = parse_invoice_totals(LET.parse(str(path)))

    assert totals["net"] == Decimal("100.00")
    assert totals["vat"] == Decimal("22.00")
    assert totals["gross"] == Decimal("122.00")
    assert totals["mismatch"] is False


def _write_allowance_invoice(path: Path) -> Path:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_LIN><D_1082>1</D_1082></S_LIN>"
        "      <S_QTY><C_C186><D_6063>47</D_6063><D_6060>1.00</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <G_SG27>"
        "        <S_MOA><C_C516><D_5025>203</D_5025><D_5004>100.00</D_5004></C_C516></S_MOA>"
        "      </G_SG27>"
        "      <G_SG34>"
        "        <S_TAX><D_5283>7</D_5283><C_C241><D_5153>VAT</D_5153></C_C241><C_C243><D_5278>20.00</D_5278></C_C243><D_5305>S</D_5305></S_TAX>"
        "        <S_MOA><C_C516><D_5025>124</D_5025><D_5004>8.00</D_5004></C_C516></S_MOA>"
        "        <S_MOA><C_C516><D_5025>125</D_5025><D_5004>40.00</D_5004></C_C516></S_MOA>"
        "      </G_SG34>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463><C_C552><D_5189>95</D_5189></C_C552></S_ALC>"
        "        <G_SG42>"
        "          <S_MOA><C_C516><D_5025>204</D_5025><D_5004>60.00</D_5004></C_C516></S_MOA>"
        "        </G_SG42>"
        "      </G_SG39>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>40.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025><D_5004>48.00</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_TAX><D_5283>7</D_5283><C_C241><D_5153>VAT</D_5153></C_C241><C_C243><D_5278>20.00</D_5278></C_C243><D_5305>S</D_5305></S_TAX>"
        "      <S_MOA><C_C516><D_5025>124</D_5025><D_5004>8.00</D_5004></C_C516></S_MOA>"
        "      <S_MOA><C_C516><D_5025>125</D_5025><D_5004>40.00</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    path.write_text(xml)
    return path


def test_parse_eslog_invoice_respects_line_allowance_header_totals(tmp_path):
    path = _write_allowance_invoice(tmp_path / "allowance.xml")

    df, ok = parse_eslog_invoice(path)

    assert ok
    assert df.attrs["gross_calc"] == Decimal("48.00")
    assert not df.attrs.get("gross_mismatch", False)
    assert df["vrednost"].sum() == Decimal("40.00")


def test_parse_invoice_totals_uses_header_amounts_with_allowance(tmp_path):
    path = _write_allowance_invoice(tmp_path / "allowance_totals.xml")

    tree = LET.parse(str(path))
    totals = parse_invoice_totals(tree)

    assert totals["net"] == Decimal("40.00")
    assert totals["vat"] == Decimal("8.00")
    assert totals["gross"] == Decimal("48.00")
    assert totals["mismatch"] is False

