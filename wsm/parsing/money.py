# File: wsm/parsing/money.py
from decimal import Decimal, ROUND_HALF_UP
from lxml import etree as LET
import pandas as pd


def round_to_step(
    value: Decimal, step: Decimal, rounding=ROUND_HALF_UP
) -> Decimal:
    """Round ``value`` to the nearest ``step`` (e.g. 0.01 or 0.05)."""
    if step == 0:
        return value
    quant = (value / step).quantize(Decimal("1"), rounding=rounding)
    return (quant * step).quantize(step)


def detect_round_step(reference: Decimal, candidate: Decimal) -> Decimal:
    """Return the rounding step (0.01 or 0.05) that makes ``candidate`` match
    ``reference`` if possible.  If neither matches exactly, return 0.05 as a
    safe default."""
    for step in (Decimal("0.01"), Decimal("0.05")):
        if round_to_step(candidate, step) == reference:
            return step
    return Decimal("0.05")


def quantize_like(
    value: Decimal, reference: Decimal, rounding=ROUND_HALF_UP
) -> Decimal:
    """Quantize ``value`` with the same precision as ``reference``."""
    quant = Decimal("1").scaleb(reference.as_tuple().exponent)
    return value.quantize(quant, rounding=rounding)


def extract_total_amount(xml_root: LET._Element) -> Decimal:
    """
    Prebere osnovno glavo (<InvoiceTotal>) in, če obstaja, odšteje vrednost iz
    <DocumentDiscount>. Če katerikoli manjka, privzame 0.00.
    """
    base_el = xml_root.find("InvoiceTotal")
    discount_el = xml_root.find("DocumentDiscount")

    base_str = base_el.text if base_el is not None else None
    discount_str = discount_el.text if discount_el is not None else None

    def _find_moa_values(codes: set[str]) -> Decimal:
        total = Decimal("0")
        for seg in xml_root.iter():
            if seg.tag.split("}")[-1] != "S_MOA":
                continue
            code = None
            amount = None
            for el in seg.iter():
                tag = el.tag.split("}")[-1]
                if tag == "D_5025":
                    code = (el.text or "").strip()
                elif tag == "D_5004":
                    amount = (el.text or "").strip()
            if code in codes and amount is not None:
                total += Decimal(amount.replace(",", "."))
        return total

    base = (
        Decimal(base_str.replace(",", "."))
        if base_str not in (None, "")
        else _find_moa_values({"79"})
    )
    discount = (
        Decimal(discount_str.replace(",", "."))
        if discount_str not in (None, "")
        else _find_moa_values({"176", "204", "260", "500"})
    )

    return (base - discount).quantize(Decimal("0.01"))


def extract_line_items(xml_root: LET._Element) -> pd.DataFrame:
    """
    Iz <LineItems> vsak <LineItem> prebere 'PriceNet', 'Quantity', 'DiscountPct'
    in izračuna izracunana_vrednost = price_net * quantity * (1 - discount_pct/100).
    Vrne DataFrame s stolpci:
      - cena_netto (Decimal)
      - kolicina   (Decimal)
      - rabata_pct (Decimal)
      - izracunana_vrednost (Decimal)
    """
    rows = []
    for li in xml_root.findall("LineItems/LineItem"):
        price_str = li.findtext("PriceNet") or "0.00"
        qty_str = li.findtext("Quantity") or "0.00"
        discount_pct_str = li.findtext("DiscountPct") or "0.00"

        cena = Decimal(price_str.replace(",", "."))
        kolic = Decimal(qty_str.replace(",", "."))
        rabata_pct = Decimal(discount_pct_str.replace(",", "."))

        izracun_val = (
            cena * kolic * (Decimal("1") - rabata_pct / Decimal("100"))
        ).quantize(Decimal("0.01"))

        rows.append(
            {
                "cena_netto": cena,
                "kolicina": kolic,
                "rabata_pct": rabata_pct,
                "izracunana_vrednost": izracun_val,
            }
        )

    return pd.DataFrame(rows, dtype=object)


def validate_invoice(df: pd.DataFrame, header_total: Decimal) -> bool:
    """Validate that the sum of line values matches ``header_total`` using
    the rounding step that best fits the invoice."""
    # 1) Pretvorimo stolpec iz float v Decimal (če obstaja)
    if "izracunana_vrednost" not in df.columns:
        return False

    df["izracunana_vrednost"] = df["izracunana_vrednost"].apply(
        lambda x: Decimal(str(x))
    )

    # 2) Vsoto pretvorimo v Decimal, četudi je sum() vrnil int
    total_sum = df["izracunana_vrednost"].sum()
    # Če sum vrne int ali float, ga pretvorimo v Decimal; če je že Decimal, OK
    if not isinstance(total_sum, Decimal):
        total_sum = Decimal(str(total_sum))
    line_sum = Decimal(total_sum)
    step = detect_round_step(header_total, line_sum)
    rounded = round_to_step(line_sum, step)

    # 3) Primerjamo z header_total s toleranco izbranega koraka
    return abs(rounded - header_total) <= step
