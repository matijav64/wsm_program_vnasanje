"""Enumerations for ESLOG codes used in parsing."""

from enum import Enum


class Moa(str, Enum):
    """Relevant MOA segment codes."""

    GROSS = "38"
    DISCOUNT = "204"
    VAT = "124"
    NET = "203"
    HEADER_NET = "389"
    GRAND_TOTAL = "9"


class Dtm(str, Enum):
    """Relevant DTM codes."""

    SERVICE_DATE = "35"
    INVOICE_DATE = "137"
