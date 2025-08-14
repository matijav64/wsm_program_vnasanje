from decimal import Decimal
from lxml import etree as LET

from wsm.parsing.eslog import sum_moa


def test_sum_moa_handles_groups_without_namespace():
    xml = (
        "<Invoice>"
        "  <M_INVOIC>"
        "    <G_SG50>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025>"
        "        <D_5004>-1.50</D_5004></C_C516></S_MOA>"
        "    </G_SG50>"
        "    <G_SG20>"
        "      <S_ALC><D_5463>A</D_5463></S_ALC>"
        "      <S_MOA><C_C516><D_5025>204</D_5025>"
        "        <D_5004>-2.00</D_5004></C_C516></S_MOA>"
        "    </G_SG20>"
        "  </M_INVOIC>"
        "</Invoice>"
    )
    root = LET.fromstring(xml)
    total = sum_moa(root, ["204"])
    assert total == Decimal("-3.50")
