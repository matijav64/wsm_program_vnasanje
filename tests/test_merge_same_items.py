from decimal import Decimal
import pandas as pd

from wsm.ui.review.helpers import _merge_same_items


def test_merge_same_items_merges_duplicates_keeps_gratis():
    df = pd.DataFrame(
        [
            {
                "wsm_sifra": "A",
                "naziv": "ItemA",
                "naziv_ckey": "itema",
                "enota_norm": "kos",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "A",
                "naziv": "ItemA",
                "naziv_ckey": "itema",
                "enota_norm": "kos",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "B",
                "naziv": "ItemB",
                "naziv_ckey": "itemb",
                "enota_norm": "kos",
                "kolicina": Decimal("2"),
                "kolicina_norm": Decimal("2"),
                "vrednost": Decimal("20"),
                "rabata": Decimal("0"),
                "total_net": Decimal("20"),
                "ddv": Decimal("4.4"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "B",
                "naziv": "ItemB",
                "naziv_ckey": "itemb",
                "enota_norm": "kos",
                "kolicina": Decimal("2"),
                "kolicina_norm": Decimal("2"),
                "vrednost": Decimal("20"),
                "rabata": Decimal("0"),
                "total_net": Decimal("20"),
                "ddv": Decimal("4.4"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "C",
                "naziv": "Free",
                "naziv_ckey": "free",
                "enota_norm": "kos",
                "kolicina": Decimal("1"),
                "kolicina_norm": Decimal("1"),
                "vrednost": Decimal("0"),
                "rabata": Decimal("0"),
                "total_net": Decimal("0"),
                "ddv": Decimal("0"),
                "is_gratis": True,
            },
            {
                "wsm_sifra": "C",
                "naziv": "Free",
                "naziv_ckey": "free",
                "enota_norm": "kos",
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
    merged_a = result[
        (result["wsm_sifra"] == "A") & (~result["is_gratis"])
    ].iloc[0]
    assert merged_a["kolicina"] == Decimal("2")
    assert merged_a["total_net"] == Decimal("20")
    assert merged_a["ddv"] == Decimal("4.4")

    merged_b = result[
        (result["wsm_sifra"] == "B") & (~result["is_gratis"])
    ].iloc[0]
    assert merged_b["kolicina"] == Decimal("4")
    assert merged_b["total_net"] == Decimal("40")
    assert merged_b["ddv"] == Decimal("8.8")

    # Gratis lines should merge with each other but stay separate from paid
    merged_c = result[
        (result["wsm_sifra"] == "C") & (result["is_gratis"])
    ].iloc[0]
    assert merged_c["kolicina"] == Decimal("2")
    assert merged_c["total_net"] == Decimal("0")
    assert merged_c["ddv"] == Decimal("0")
    assert len(result) == 3

    # VAT should sum across merged rows as expected
    assert result["ddv"].sum() == Decimal("13.2")


def test_merge_same_items_handles_none_in_numeric_columns():
    df = pd.DataFrame(
        [
            {
                "wsm_sifra": "A",
                "naziv": "ItemA",
                "naziv_ckey": "itema",
                "enota_norm": "kos",
                "kolicina": None,
                "kolicina_norm": None,
                "vrednost": Decimal("10"),
                "rabata": Decimal("0"),
                "total_net": Decimal("10"),
                "ddv": Decimal("2.2"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "A",
                "naziv": "ItemA",
                "naziv_ckey": "itema",
                "enota_norm": "kos",
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


def test_merge_same_items_groups_by_discount_not_price():
    df = pd.DataFrame(
        [
            {
                "sifra_dobavitelja": "123",
                "naziv_ckey": "paprika",
                "enota_norm": "kg",
                "wsm_sifra": "PAP",
                "is_gratis": False,
                "kolicina_norm": Decimal("1"),
                "total_net": Decimal("2.251"),
                "cena_po_rabatu": Decimal("2.251"),
                "eff_discount_pct": Decimal("0"),
                "rabata_pct": Decimal("0"),
            },
            {
                "sifra_dobavitelja": "123",
                "naziv_ckey": "paprika",
                "enota_norm": "kg",
                "wsm_sifra": "PAP",
                "is_gratis": False,
                "kolicina_norm": Decimal("1"),
                "total_net": Decimal("1.950"),
                "cena_po_rabatu": Decimal("1.950"),
                "eff_discount_pct": Decimal("0"),
                "rabata_pct": Decimal("0"),
            },
        ]
    )

    result = _merge_same_items(df)

    assert len(result) == 1
    merged = result.iloc[0]
    assert merged["kolicina_norm"] == Decimal("2")
    assert merged["total_net"] == Decimal("4.201")
    assert merged["cena_po_rabatu"] == Decimal("2.101")


def test_merge_same_items_tracks_returns():
    df = pd.DataFrame(
        [
            {
                "wsm_sifra": "X",
                "naziv_ckey": "pivo",
                "enota_norm": "L",
                "kolicina_norm": Decimal("20"),
                "vrednost": Decimal("30"),
                "rabata": Decimal("0"),
                "total_net": Decimal("30"),
                "ddv": Decimal("6"),
                "is_gratis": False,
            },
            {
                "wsm_sifra": "X",
                "naziv_ckey": "pivo",
                "enota_norm": "L",
                "kolicina_norm": Decimal("-20"),
                "vrednost": Decimal("-30"),
                "rabata": Decimal("0"),
                "total_net": Decimal("-30"),
                "ddv": Decimal("-6"),
                "is_gratis": False,
            },
        ]
    )

    merged = _merge_same_items(df)
    assert merged.loc[0, "kolicina_norm"] == Decimal("0")
    assert merged.loc[0, "vrnjeno"] == Decimal("20")



def test_merge_same_items_preserves_discount_dimension():
    import wsm.ui.review.helpers as h

    h.GROUP_BY_DISCOUNT = True
    df = pd.DataFrame(
        {
            "wsm_sifra": ["1", "1"],
            "enota_norm": ["kos", "kos"],
            "is_gratis": [False, False],
            "kolicina": [Decimal("1"), Decimal("1")],
            "kolicina_norm": [Decimal("1"), Decimal("1")],
            "vrednost": [Decimal("10"), Decimal("10")],
            "rabata": [Decimal("2"), Decimal("1")],
            "rabata_pct": [Decimal("20"), Decimal("10")],
            "total_net": [Decimal("8"), Decimal("9")],
            "ddv": [Decimal("1.76"), Decimal("1.98")],
        }
    )
    merged = _merge_same_items(df)
    assert len(merged) == 2
