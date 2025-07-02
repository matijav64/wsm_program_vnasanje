"""Project-wide constants."""
from decimal import Decimal
from pathlib import Path
import csv

# Threshold for price change warnings (percent).
PRICE_DIFF_THRESHOLD = Decimal("1.0")

# Mapping of supplier item codes to their weight per individual piece (in kg).
# Add entries as needed for products where the packaging weight is constant.
WEIGHTS_PER_PIECE: dict[tuple[str, str], Decimal] = {}

csv_path = Path(__file__).resolve().parent / ".." / "weights_per_piece.csv"
csv_path = csv_path.resolve()
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
