"""Project-wide constants."""

from decimal import Decimal
from pathlib import Path
from os import getenv
import csv


def _env_bool(name: str, default: str | None = None) -> bool:
    """Return a boolean flag read from the environment."""

    value = getenv(name)
    if value is None:
        value = default if default is not None else "0"
    value = str(value).strip().lower()
    return value not in {"0", "false", "no", "off", ""}


def _env_decimal(name: str, default: Decimal | str) -> Decimal:
    """Return a non-negative :class:`Decimal` read from the environment."""

    fallback = Decimal(str(default))
    raw = getenv(name)
    if raw is None or str(raw).strip() == "":
        return abs(fallback)
    try:
        normalized = str(raw).strip().replace(",", ".")
        value = Decimal(normalized)
    except Exception:
        return abs(fallback)
    return abs(value) if value.is_finite() else abs(fallback)

# Threshold for price-change warnings (percent).
PRICE_DIFF_THRESHOLD = Decimal("1.0")

# Invoice tolerance configuration (overridable via environment variables).
DEFAULT_TOLERANCE = _env_decimal("WSM_TOLERANCE", "0.01")
TOLERANCE_BASE = _env_decimal("WSM_TOLERANCE_BASE", "0.02")
MAX_TOLERANCE = _env_decimal("WSM_MAX_TOLERANCE", "0.50")
SMART_TOLERANCE_ENABLED = _env_bool(
    "WSM_SMART_TOLERANCE",
    default=getenv("WSM_DYNAMIC_TOLERANCE", "1"),
)
ROUNDING_CORRECTION_ENABLED = _env_bool("WSM_AUTO_ROUNDING", "0")

# Mapping of supplier item codes to their weight per individual piece (in kg).
# Add entries as needed for products where the packaging weight is constant.
WEIGHTS_PER_PIECE: dict[tuple[str, str], Decimal] = {}

# ----------------------------------------------------------------------
#  CSV z maso artiklov je zdaj shranjen v paketu pod  wsm/data/…
# ----------------------------------------------------------------------
csv_path = Path(__file__).parent / "data" / "weights_per_piece.csv"

if csv_path.exists():
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("sifra_dobavitelja") or "").strip()
            name = (row.get("naziv_ckey") or "").strip().lower()
            weight = row.get("kg_per_piece")
            try:
                weight_dec = Decimal(str(weight))
            except Exception:
                continue
            if code and name:
                WEIGHTS_PER_PIECE[(code, name)] = weight_dec
