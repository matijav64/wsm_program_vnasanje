# WSM – Program za vnašanje računov

Ta repozitorij vsebuje kodo aplikacije **WSM** (White-label Supplier Manager),
ki avtomatizira vnos in obdelavo računov ter povezovanje s šiframi izdelkov.

## Struktura projekta

- `wsm/` – glavni paket z vsemi Python modulčki:
  - `__main__.py` – entry point, ki pokliče CLI
  - `cli.py` – definicija ukazne vrstice
  - `run.py` – skripta za neposredno zaganjanje
  - `discounts.py`, `utils.py`, `money.py`, `eslog.py`, `pdf.py` ipd.
  - `ui/` – modul za GUI (recenzija in povezovanje povezav)
  - `parsing/` – modul za parsanje e-računov (XML/PDF)
- `requirements.txt` – odvisnosti, ki jih potrebuje aplikacija
- `pyproject.toml` – (če se uporablja Poetry ali druga orodja)
- `.gitignore` – seznam ignoriranih datotek (virtualno okolje, začasne datoteke ipd.)

## Namestitev in zagon

1. Kloniraj repozitorij:
   ```bash
   git clone https://github.com/matijav64/wsm_program_vnasanje.git
   cd wsm_program_vnasanje
   ```
2. (Opcijsko) ustvari virtualno okolje in namesti odvisnosti:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   Med glavnimi odvisnostmi so `pandas`, `pdfplumber` in `openpyxl`.

3. Za osnovno validacijo računov lahko zaženete:
   ```bash
   python -m wsm.cli validate <mapa_z_racuni>
   ```
   kjer `<mapa_z_racuni>` vsebuje XML ali PDF datoteke z e‑računi.

