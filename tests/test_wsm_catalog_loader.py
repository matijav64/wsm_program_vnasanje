import pytest

pytest.importorskip("openpyxl")

import pandas as pd
from io import BytesIO

from wsm.io import load_catalog, load_keywords_map


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


@pytest.mark.parametrize("kw_header", ["Ključna beseda", "Kljucna beseda", "keyword"])
def test_load_keywords_map_aliases_and_dedup(kw_header):
    df = pd.DataFrame(
        {
            "Šifra": ["1", "2", "3"],
            kw_header: ["Foo", "foo", "Bar"],
        }
    )
    buf = _to_excel_bytes(df)
    mapping = load_keywords_map(buf)
    assert mapping == {"foo": "2", "bar": "3"}
