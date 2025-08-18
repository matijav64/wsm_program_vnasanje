import pytest

pytest.importorskip("openpyxl")

import logging  # noqa: E402
import pandas as pd  # noqa: E402
from io import BytesIO  # noqa: E402

from wsm.io import load_catalog, load_keywords_map  # noqa: E402


def _to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_load_catalog_alternate_headers_and_decimal():
    df = pd.DataFrame(
        {
            "WSM šifra": ["001"],
            "Naziv": ["Test"],
            "Zadnja nabavna cena": ["1,1232"],
        }
    )
    buf = _to_excel_bytes(df)
    result = load_catalog(buf)
    assert list(result.columns) == ["wsm_sifra", "wsm_naziv", "cena"]
    assert result.loc[0, "cena"] == pytest.approx(1.1232)


@pytest.mark.parametrize(
    "kw_header", ["Ključna beseda", "Kljucna beseda", "keyword"]
)
def test_load_keywords_map_aliases_and_duplicate_warning(kw_header, caplog):
    df = pd.DataFrame(
        {
            "Šifra": ["1", "2", "3"],
            kw_header: ["Foo", "foo", "Bar"],
        }
    )
    buf = _to_excel_bytes(df)
    with caplog.at_level(logging.WARNING):
        mapping = load_keywords_map(buf)
    assert mapping == {"foo": "1", "bar": "3"}
    assert "Duplicate keyword 'foo'" in caplog.text


def test_load_keywords_map_filters_by_supplier():
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["A", "B"],
            "wsm_sifra": ["1", "2"],
            "keyword": ["Foo", "Bar"],
        }
    )
    buf = _to_excel_bytes(df)
    mapping = load_keywords_map(buf, supplier_code="A")
    assert mapping == {"foo": "1"}
