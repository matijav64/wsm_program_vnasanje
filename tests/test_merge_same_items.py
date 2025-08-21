from decimal import Decimal
import pandas as pd

from wsm.ui.review.helpers import _merge_same_items


def test_merge_same_items_merges_duplicates_keeps_gratis():
    df = pd.DataFrame(
        [
            {
                "code": "A",
                "naziv": "ItemA",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "code": "A",
                "naziv": "ItemA",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "code": "B",
                "naziv": "ItemB",
                "kolicina": Decimal("2"),
                "kolicina_norm": Decimal("2"),
                "vrednost": Decimal("20"),
                "rabata": Decimal("0"),
                "total_net": Decimal("20"),
                "ddv": Decimal("4.4"),
                "is_gratis": False,
            },
            {
                "code": "B",
                "naziv": "ItemB",
                "kolicina": Decimal("2"),
                "kolicina_norm": Decimal("2"),
                "vrednost": Decimal("20"),
                "rabata": Decimal("0"),
                "total_net": Decimal("20"),
                "ddv": Decimal("4.4"),
                "is_gratis": False,
            },
            {
                "code": "C",
                "naziv": "Free",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("0"),
                "rabata": Decimal("0"),
                "total_net": Decimal("0"),
                "ddv": Decimal("0"),
                "is_gratis": True,
            },
            {
                "code": "C",
                "naziv": "Free",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("0"),
                "rabata": Decimal("0"),
                "total_net": Decimal("0"),
                "ddv": Decimal("0"),
                "is_gratis": True,
            },
        ]
    )

    result = _merge_same_items(df)

    # Two non-gratis groups should be merged into single rows
    merged_a = result[(result["code"] == "A") & (~result["is_gratis"])].iloc[0]
    assert merged_a["kolicina"] == Decimal("2")
    assert merged_a["total_net"] == Decimal("20")
    assert merged_a["ddv"] == Decimal("4.4")

    merged_b = result[(result["code"] == "B") & (~result["is_gratis"])].iloc[0]
    assert merged_b["kolicina"] == Decimal("4")
    assert merged_b["total_net"] == Decimal("40")
    assert merged_b["ddv"] == Decimal("8.8")

    # Gratis lines should remain unmodified and separate
    gratis_expected = df[df["is_gratis"]].reset_index(drop=True)
    gratis_result = result[result["is_gratis"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        gratis_result.sort_index(axis=1), gratis_expected.sort_index(axis=1)
    )
    assert len(result) == 4

    # VAT should sum across merged rows as expected
    assert result["ddv"].sum() == Decimal("13.2")


def test_merge_same_items_handles_none_in_numeric_columns():
    df = pd.DataFrame(
        [
            {
                "code": "A",
                "naziv": "ItemA",
                "kolicina": None,
                "kolicina_norm": None,
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "code": "A",
                "naziv": "ItemA",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
        ]
    )

    result = _merge_same_items(df)

    assert len(result) == 1
    merged = result.iloc[0]
    assert merged["kolicina"] == Decimal("1")
    assert merged["kolicina_norm"] == Decimal("1")
    assert merged["vrednost"] == Decimal("20")
    assert merged["total_net"] == Decimal("20")
    assert merged["ddv"] == Decimal("4.4")
