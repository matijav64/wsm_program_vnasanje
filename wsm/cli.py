
import logging
from decimal import Decimal

log = logging.getLogger("wsm.cli")

# Simulirani podatki za primer (odstrani in zamenjaj z dejanskimi spremenljivkami v tvojem okolju)
neto_items = Decimal("958.11")
line_rebate = Decimal("53.82")
doc_rebate = Decimal("0.00")
total_rebate = line_rebate + doc_rebate
header_neto = Decimal("958.11")
has_line_discounts = True  # Nastavi ustrezno glede na logiko
neto_real = neto_items - total_rebate

log.info("\n============ POVZETEK POPUSTOV ============")

if has_line_discounts:
    log.info(f"Skupni NETO brez popustov : ni na voljo – cene že vsebujejo popuste")
else:
    log.info(f"Skupni NETO brez popustov : {(neto_items + line_rebate):,.2f} €")

log.info(f"Skupni POPUST            : {total_rebate:,.2f} € "
         f"({line_rebate:,.2f} € vrstični + {doc_rebate:,.2f} € dokument)")
log.info(f"Skupni NETO s popusti    : {neto_real:,.2f} € ← izračunano iz postavk")
log.info("===========================================\n")

if header_neto:
    znak = "✓" if abs(header_neto - neto_real) < Decimal("0.05") else "✗"
    log.info(f"Glava po popustu: {header_neto:.2f} €  "
             f"vs. Izračunano: {neto_real:.2f} € → {znak}")


def main():
    """Delegate execution to the full CLI implementation."""
    from wsm_program_vnasanje.wsm.cli import main as real_main
    real_main()
