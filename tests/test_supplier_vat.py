from pathlib import Path
from decimal import Decimal
import pandas as pd
import pytest
from lxml import etree as LET
import wsm.ui.review.gui as rl
from wsm.parsing.eslog import get_supplier_info_vat, get_supplier_info


def test_get_supplier_info_vat_prefers_seller():
    xml = Path("tests/PR5918-Slika2.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI29746507"


def test_get_supplier_info_vat_uses_se_when_su_missing():
    xml = Path("tests/SE_after_SU.XML")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI11111111"


def test_get_supplier_info_prefers_vat_over_gln():
    xml = Path("tests/vat_with_gln.xml")
    tree = LET.parse(xml)
    code = get_supplier_info(tree)
    assert code == "SI33333333"


def test_get_supplier_info_uses_vat_when_no_gln():
    xml = Path("tests/vat_ahp_before_va.xml")
    tree = LET.parse(xml)
    code = get_supplier_info(tree)
    assert code == "SI22222222"


def test_get_supplier_info_ignores_invalid_vat(tmp_path):
    xml_content = """
    <Invoice xmlns='urn:eslog:2.00'>
      <M_INVOIC>
        <G_SG2>
          <S_NAD>
            <S_GLN><D_7402>1234567890123</D_7402></S_GLN>
            <D_3035>SE</D_3035>
          </S_NAD>
          <G_SG3>
            <S_RFF>
              <C_C506>
                <D_1153>VA</D_1153>
                <D_1154>SI123</D_1154>
              </C_C506>
            </S_RFF>
          </G_SG3>
        </G_SG2>
      </M_INVOIC>
    </Invoice>
    """
    xml_file = tmp_path / "invalid.xml"
    xml_file.write_text(xml_content)
    tree = LET.parse(xml_file)
    code = get_supplier_info(tree)
    assert code == "1234567890123"
    _, _, vat = get_supplier_info_vat(xml_file)
    assert vat is None


def test_get_supplier_info_vat_handles_plain_rff():
    xml = Path("tests/Racun_st._25-24412.xml")
    _, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI47083026"


def test_get_supplier_info_vat_reads_ubl_vat():
    xml = Path("tests/ubl_vat.xml")
    code, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI99999999"
    assert code == "SI99999999"


def test_get_supplier_info_vat_reads_ubl_va_scheme():
    xml = Path("tests/ubl_vat_va.xml")
    code, _, vat = get_supplier_info_vat(xml)
    assert vat == "SI69092958"
    assert code == "SI69092958"


def _dummy_df():
    return pd.DataFrame(
        {
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Item"],
            "kolicina": [Decimal("1")],
            "enota": ["kos"],
            "vrednost": [Decimal("1")],
            "rabata": [Decimal("0")],
            "ddv": [Decimal("0")],
            "ddv_stopnja": [Decimal("0")],
            "sifra_artikla": [pd.NA],
        }
    )


def test_review_links_prefers_vat(monkeypatch, tmp_path, caplog):
    invoice = Path("tests/ubl_vat.xml")
    links_file = tmp_path / "sup" / "code" / "links.xlsx"
    links_file.parent.mkdir(parents=True)
    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr(rl, "_build_header_totals", lambda *a, **k: {})
    monkeypatch.setattr(
        rl.tk, "Tk", lambda: (_ for _ in ()).throw(RuntimeError)
    )
    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError):
            rl.review_links(
                _dummy_df(),
                pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"]),
                links_file,
                Decimal("1"),
                invoice,
            )
    assert "Resolved supplier code: SI99999999" in caplog.text


def test_review_links_falls_back_to_gln(monkeypatch, tmp_path, caplog):
    invoice = Path("tests/gln_only.xml")
    links_file = tmp_path / "sup" / "code" / "links.xlsx"
    links_file.parent.mkdir(parents=True)
    monkeypatch.setattr(rl, "_load_supplier_map", lambda p: {})
    monkeypatch.setattr(rl, "_build_header_totals", lambda *a, **k: {})
    monkeypatch.setattr(
        rl.tk, "Tk", lambda: (_ for _ in ()).throw(RuntimeError)
    )
    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError):
            rl.review_links(
                _dummy_df(),
                pd.DataFrame(columns=["wsm_sifra", "wsm_naziv"]),
                links_file,
                Decimal("1"),
                invoice,
            )
    assert "Resolved supplier code: 9876543210987" in caplog.text
