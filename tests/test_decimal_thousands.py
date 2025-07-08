from decimal import Decimal
import xml.etree.ElementTree as ET

from wsm.parsing.eslog import _decimal


def _make_el(text: str) -> ET.Element:
    return ET.fromstring(f"<x>{text}</x>")


def test_decimal_handles_dot_thousands():
    el = _make_el("1.234,56")
    assert _decimal(el) == Decimal("1234.56")


def test_decimal_handles_space_thousands():
    el = _make_el("1 234,56")
    assert _decimal(el) == Decimal("1234.56")
