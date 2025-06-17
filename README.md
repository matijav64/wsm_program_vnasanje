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

4. Za ročno povezovanje WSM šifer podajte pot do računa:
   ```bash
   python -m wsm.cli review <invoice.xml>
   ```
  (po želji dodajte `--wsm-codes pot/do/sifre_wsm.xlsx`)
  Program odpre grafični vmesnik, kjer povezave shranjujete v podmapo
  `links/<ime_dobavitelja>/`. Posodobljene tabele najdete v datotekah
  `<koda>_<ime>_povezane.xlsx` in `price_history.xlsx`.
  Okno se privzeto odpre v običajni velikosti. S tipko F11 ga lahko
  ročno preklopite v celozaslonski način, iz katerega izstopite s
  tipko Esc.


Če `--wsm-codes` ni podan, program poskuša prebrati `sifre_wsm.xlsx` v
korenu projekta.

Pri samodejnem povezovanju lahko program iz teh ročno
shranjenih datotek sam izdela datoteko `keywords.xlsx`.
Če ta datoteka ne obstaja, jo funkcija `povezi_z_wsm`
samodejno napolni z najpogostejšimi izrazi iz `*_povezane.xlsx`.

5. Analizo in združevanje postavk lahko izvedete z:
   ```bash
   python -m wsm.cli analyze <invoice.xml> --suppliers links
   ```
   Ukaz izpiše povzetek po WSM šifrah in preveri, ali se vsota ujema z
   vrednostjo na računu.

## Možne izboljšave

- **Izboljšano samodejno povezovanje**: poleg iskanja po ključnih besedah bi lahko uporabili knjižnice za "bližnje ujemanje" (npr. `rapidfuzz`), ali pa vektorsko iskanje, kot nakazuje mapa `wsm_vector_embedding`. Tako bi sistem lažje našel ustrezno WSM šifro tudi pri rahlo različnih nazivih artiklov.
- **Bolj zmogljiv GUI**: Tkinter je enostaven, a za obsežnejše tabele bi lahko razmislili o prehodu na `PyQt5`, ki omogoča naprednejše filtriranje in iskanje.
- **Testne enote za GUI**: poleg obstoječih testov za parsanje XML je smiselno dodati teste, ki preverijo logiko povezovanja (npr. ali `_save_and_close` pravilno posodobi Excel). Osnovne funkcije GUI se da avtomatizirati z unit testi.

