from decimal import Decimal
from pathlib import Path

from wsm.utils import _build_header_totals


def test_build_header_totals_extracts(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025>"
        "<D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025>"
        "<D_5004>2</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025>"
        "<D_5004>12</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    p = tmp_path / "inv.xml"
    p.write_text(xml)
    totals = _build_header_totals(p, Decimal("0"))
    assert totals == {
        "net": Decimal("10"),
        "vat": Decimal("2"),
        "gross": Decimal("12"),
    }


def test_build_header_totals_fills_missing_net(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025>"
        "<D_5004>0</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025>"
        "<D_5004>2.20</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025>"
        "<D_5004>12.206</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    p = tmp_path / "inv.xml"
    p.write_text(xml)
    totals = _build_header_totals(p, Decimal("0"))
    assert totals["net"] == Decimal("10.01")
    assert totals["vat"] == Decimal("2.20")
    assert totals["gross"] == Decimal("12.206")


def test_build_header_totals_fills_missing_gross(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025>"
        "<D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025>"
        "<D_5004>2</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025>"
        "<D_5004>0</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    p = tmp_path / "inv.xml"
    p.write_text(xml)
    totals = _build_header_totals(p, Decimal("0"))
    assert totals["net"] == Decimal("10")
    assert totals["vat"] == Decimal("2")
    assert totals["gross"] == Decimal("12")


def test_build_header_totals_fills_missing_vat(tmp_path: Path) -> None:
    xml = (
        "<Invoice xmlns='urn:eslog:2.00'>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>389</D_5025>"
        "<D_5004>10</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG52>"
        "      <S_MOA><C_C516><D_5025>124</D_5025>"
        "<D_5004>0</D_5004></C_C516></S_MOA>"
        "    </G_SG52>"
        "    <G_SG50>"
        "      <S_MOA><C_C516><D_5025>9</D_5025>"
        "<D_5004>12</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    p = tmp_path / "inv.xml"
    p.write_text(xml)
    totals = _build_header_totals(p, Decimal("0"))
    assert totals["net"] == Decimal("10")
    assert totals["vat"] == Decimal("2")
    assert totals["gross"] == Decimal("12")
