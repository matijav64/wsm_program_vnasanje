from decimal import Decimal
import builtins
import pytest
from wsm.parsing.eslog import parse_invoice


def test_parse_invoice_eslog_string_no_fs(monkeypatch):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00' "
        "xmlns:cbc='urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>"
        "      <G_SG34>"
        "        <cbc:TaxAmount>2.20</cbc:TaxAmount>"
        "        <S_TAX><C_C243><D_5278>22</D_5278></C_C243></S_TAX>"
        "      </G_SG34>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>9</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>-1</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )

    def fail_open(*args, **kwargs):
        raise AssertionError("filesystem access")

    monkeypatch.setattr(builtins, "open", fail_open)

    df, header_total, discount_total, gross_total = parse_invoice(xml)
    assert header_total == Decimal("9")
    assert discount_total == Decimal("1")
    assert not df.empty
    assert "ddv" in df.columns
    assert df["ddv"].iloc[0] == Decimal("2.20")
