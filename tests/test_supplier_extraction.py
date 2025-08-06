from lxml import etree as LET

from wsm.parsing.eslog import get_supplier_info


def test_get_supplier_info_prefers_vat():
    xml = """
    <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
             xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
      <cac:AccountingSupplierParty>
        <cac:Party>
          <cac:PartyIdentification>
            <cbc:ID schemeID="0088">1234567890005</cbc:ID>
          </cac:PartyIdentification>
          <cac:PartyTaxScheme>
            <cbc:CompanyID schemeID="VA">SI69092958</cbc:CompanyID>
          </cac:PartyTaxScheme>
        </cac:Party>
      </cac:AccountingSupplierParty>
    </Invoice>
    """
    tree = LET.ElementTree(LET.fromstring(xml))
    assert get_supplier_info(tree) == "SI69092958"
