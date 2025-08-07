from decimal import Decimal
import io
import builtins
import pytest
from wsm.parsing.eslog import parse_invoice


def test_parse_invoice_eslog_string_no_fs(monkeypatch):
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG26>"
        "      <S_QTY><C_C186><D_6060>1</D_6060><D_6411>PCE</D_6411></C_C186></S_QTY>"
        "      <S_LIN><C_C212><D_7140>1</D_7140></C_C212></S_LIN>"
        "      <S_IMD><C_C273><D_7008>Item</D_7008></C_C273></S_IMD>"
        "      <S_MOA><C_C516><D_5025>203</D_5025><D_5004>8</D_5004></C_C516></S_MOA>"
        "      <G_SG39>"
        "        <S_ALC><D_5463>A</D_5463></S_ALC>"
        "        <G_SG42>"
        "          <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>"
        "        </G_SG42>"
        "      </G_SG39>"
        "    </G_SG26>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025><D_5004>7</D_5004></C_C516></S_MOA>"
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

    df, header_total, discount_total = parse_invoice(xml)
    assert header_total == Decimal("7")
    assert discount_total == Decimal("1")
    assert not df.empty
