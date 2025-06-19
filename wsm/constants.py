"""Project-wide constants."""
from decimal import Decimal

# Mapping of supplier item codes to their weight per individual piece (in kg).
# Add entries as needed for products where the packaging weight is constant.
WEIGHTS_PER_PIECE: dict[str, Decimal] = {
    # Example:
    # "12345": Decimal("0.5"),  # 0.5 kg per piece for supplier code 12345
}
