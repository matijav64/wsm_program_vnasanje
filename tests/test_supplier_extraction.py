import pytest
from lxml import etree

from wsm.parsing.eslog import get_supplier_info

UBL_NSMAP = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
CAC = UBL_NSMAP["cac"]
CBC = UBL_NSMAP["cbc"]


@pytest.fixture
def supplier_with_vat_and_gln():
    invoice = etree.Element("Invoice", nsmap=UBL_NSMAP)
    party = etree.SubElement(
        etree.SubElement(invoice, f"{{{CAC}}}AccountingSupplierParty"),
        f"{{{CAC}}}Party",
    )
    tax_scheme = etree.SubElement(party, f"{{{CAC}}}PartyTaxScheme")
    etree.SubElement(tax_scheme, f"{{{CBC}}}CompanyID").text = "SI69092958"
    identification = etree.SubElement(party, f"{{{CAC}}}PartyIdentification")
    etree.SubElement(
        identification, f"{{{CBC}}}ID", schemeID="0088"
    ).text = "3830045969997"
    return invoice


def test_vat_priority(supplier_with_vat_and_gln):
    assert get_supplier_info(supplier_with_vat_and_gln) == "SI69092958"


def test_gln_fallback():
    invoice = etree.Element("Invoice", nsmap=UBL_NSMAP)
    party = etree.SubElement(
        etree.SubElement(invoice, f"{{{CAC}}}AccountingSupplierParty"),
        f"{{{CAC}}}Party",
    )
    identification = etree.SubElement(party, f"{{{CAC}}}PartyIdentification")
    etree.SubElement(
        identification, f"{{{CBC}}}ID", schemeID="0088"
    ).text = "3830045969997"
    assert get_supplier_info(invoice) == "3830045969997"


def test_custom_va_tag():
    invoice = etree.Element("Invoice")
    etree.SubElement(invoice, "VA").text = "SI69092958"
    assert get_supplier_info(invoice) == "SI69092958"


def test_unknown_supplier():
    invoice = etree.Element("Invoice")
    assert get_supplier_info(invoice) == "Unknown"

