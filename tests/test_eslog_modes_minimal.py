from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import parse_invoice_totals


def test_eslog_modes_minimal() -> None:
    # Minimal E-SLOG, usklajen s parserjem:
    # - namespace: urn:edifact:xml:enriched (prefix e)
    # - header totals: G_SG34 z MOA 125/124/9
    # - ena vrstica z MOA 203
    xml = """
<e:INVOIC xmlns:e="urn:edifact:xml:enriched">
  <e:G_SG34>
    <e:S_MOA>
      <e:C_C516>
        <e:D_5025>125</e:D_5025>
        <e:D_5004>10.00</e:D_5004>
      </e:C_C516>
    </e:S_MOA>
    <e:S_MOA>
      <e:C_C516>
        <e:D_5025>124</e:D_5025>
        <e:D_5004>2.20</e:D_5004>
      </e:C_C516>
    </e:S_MOA>
    <e:S_MOA>
      <e:C_C516>
        <e:D_5025>9</e:D_5025>
        <e:D_5004>12.20</e:D_5004>
      </e:C_C516>
    </e:S_MOA>
  </e:G_SG34>
  <e:G_SG26>
    <e:G_SG27>
      <e:S_MOA>
        <e:C_C516>
          <e:D_5025>203</e:D_5025>
          <e:D_5004>10.00</e:D_5004>
        </e:C_C516>
      </e:S_MOA>
    </e:G_SG27>
  </e:G_SG26>
</e:INVOIC>
"""
    root = LET.fromstring(xml)
    tree = LET.ElementTree(root)
    totals = parse_invoice_totals(tree)
    assert totals["net"] == Decimal("10.00")
    assert totals["vat"] == Decimal("2.20")
    assert totals["gross"] == Decimal("12.20")
    assert not totals["mismatch"]

