# flake8: noqa
import pytest
from lxml import etree

from wsm.parsing.eslog import get_supplier_info


@pytest.fixture
def sample_xml():
    xml_str = """
<Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>SI69092958</cbc:CompanyID>
      </cac:PartyTaxScheme>
      <cac:PartyIdentification>
        <cbc:ID schemeID="0088">3830045969997</cbc:ID>
      </cac:PartyIdentification>
    </cac:Party>
  </cac:AccountingSupplierParty>
</Invoice>
    """
    return etree.fromstring(xml_str.encode())


def test_get_supplier_info_prioritizes_vat(sample_xml):
    code = get_supplier_info(sample_xml)
    assert code == "SI69092958"


def test_get_supplier_info_fallback_to_gln():
    xml_str = """
<Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyIdentification>
        <cbc:ID schemeID="0088">3830045969997</cbc:ID>
      </cac:PartyIdentification>
    </cac:Party>
  </cac:AccountingSupplierParty>
</Invoice>
    """
    tree = etree.fromstring(xml_str.encode())
    code = get_supplier_info(tree)
    assert code == "3830045969997"


def test_get_supplier_info_custom_va_tag():
    xml_str = """
<Invoice>
  <VA>SI69092958</VA>
</Invoice>
    """
    tree = etree.fromstring(xml_str.encode())
    code = get_supplier_info(tree)
    assert code == "SI69092958"


def test_get_supplier_info_unknown():
    xml_str = """
<Invoice />
    """
    tree = etree.fromstring(xml_str.encode())
    code = get_supplier_info(tree)
    assert code == "Unknown"
