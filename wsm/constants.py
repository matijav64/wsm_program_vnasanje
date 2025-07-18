"""Project-wide constants."""

from decimal import Decimal
from pathlib import Path
import csv

# Threshold for price-change warnings (percent).
PRICE_DIFF_THRESHOLD = Decimal("1.0")

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
