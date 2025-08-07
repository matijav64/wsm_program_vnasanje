import pandas as pd
from decimal import Decimal

from wsm.ui.review.io import _write_history_files


def test_credit_note_not_logged(tmp_path):
    df = pd.DataFrame(
        {
            "sifra_dobavitelja": ["SUP"],
            "naziv": ["Artikel"],
            "cena_netto": [Decimal("10")],
            "total_net": [Decimal("10")],
            "kolicina_norm": [1],
            "enota_norm": ["kg"],
        }
    )

    xml = tmp_path / "credit.xml"
    xml.write_text(
        "<Invoice xmlns:cbc=\"urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2\">"
        "<cbc:InvoiceTypeCode>381</cbc:InvoiceTypeCode></Invoice>"
    )

    new_folder = tmp_path / "SUP"
    new_folder.mkdir()

    links_file = new_folder / "links.xlsx"

    _write_history_files(
        df,
        xml,
        new_folder,
        links_file,
        tmp_path,
        root=None,
    )

    assert not (new_folder / "price_history.xlsx").exists()

