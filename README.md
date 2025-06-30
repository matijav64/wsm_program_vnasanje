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
   Za PyQt različico GUI lahko namestite tudi `PyQt5` preko
   ```bash
   pip install 'wsm[pyqt]'
   ```
   Za razvoj in poganjanje testov namestite tudi dodatne odvisnosti:
   ```bash
   pip install -r requirements-dev.txt
   ```


3. (Opcijsko) namestite paket v razvojni načini:
   ```bash
   pip install -e .
   ```
   Po takšni namestitvi (ali če ukaze zaganjate iz korena repozitorija) GUI
   odprete z:
   ```bash
   python -m wsm.run
   ```
   CLI orodja pa s:
   ```bash
   python -m wsm.cli
   ```

4. Za osnovno validacijo računov lahko zaženete:
   ```bash
   python -m wsm.cli validate <mapa_z_racuni>
   ```
   kjer `<mapa_z_racuni>` vsebuje XML ali PDF datoteke z e‑računi.

5. Za ročno povezovanje WSM šifer podajte pot do računa:
   ```bash
   python -m wsm.cli review <invoice.xml>
   ```
  (po želji dodajte `--wsm-codes pot/do/sifre_wsm.xlsx`,
  `--price-warn-pct <odstotek>` ali `--use-pyqt` za Qt različico)
   Program odpre grafični vmesnik, kjer povezave shranjujete v podmapo
  `links/<davcna_stevilka>/` (oziroma `links/<ime_dobavitelja>`,
  če davčna številka ni znana). Posodobljene tabele najdete v datotekah
  `<koda>_<ime>_povezane.xlsx` in `price_history.xlsx`.
  Če isti račun obdelate večkrat, program v `price_history.xlsx`
  prepozna obstoječo zgoščeno vrednost in prikaže opozorilo; drugi zapis
  je tako privzeto preskočen.
  Če davčna številka ni navedena na računu, jo lahko program prebere iz
   obstoječe datoteke `supplier.json` v ustrezni mapi povezav.
  Okno se privzeto odpre v običajni velikosti. S tipko F11 ga lahko
  ročno preklopite v celozaslonski način, iz katerega izstopite s
  tipko Esc.
  Pri vrhu okna so na voljo gumbi "Kopiraj dobavitelja", "Kopiraj storitev" in
  "Kopiraj številko računa", ki ustrezne vrednosti hitro prenesejo na odložišče.
  Gumbi so zdaj tik pod zgornjo vrstico, pod njimi pa je nekaj dodatnega
  prostora, da se ločijo od tabele.
  Ob potrditvi vrstice program primerja ceno s prejšnjimi zapisi v
  `price_history.xlsx`. Če je odstopanje večje od nastavljenega praga
  (privzeto 5&nbsp;% oz. vrednost spremenljivke `WSM_PRICE_WARN_PCT`), se
  vrstica obarva oranžno in prikaže se namig z zadnjo ceno ter razliko v
  odstotkih.
  Parameter `--price-warn-pct` omogoča začasno nastavitev drugega praga.



Če `--wsm-codes` ni podan, program poskuša prebrati `sifre_wsm.xlsx` v
korenu projekta.
Lahko pa pot do datoteke določite tudi z okoljsko spremenljivko
`WSM_CODES`. Podobno lahko z `WSM_SUPPLIERS` nastavite mapo s povezavami
do dobaviteljev. GUI in ukazi CLI privzeto upoštevajo ti spremenljivki,
če argumenti niso podani.

Za datoteko s ključnimi besedami lahko nastavite okoljsko spremenljivko
`WSM_KEYWORDS`, ki kaže na `kljucne_besede_wsm_kode.xlsx`. Če ni
nastavljena, program privzeto bere to datoteko iz trenutne mape.


Pri samodejnem povezovanju lahko program iz teh ročno
shranjenih datotek sam izdela datoteko `kljucne_besede_wsm_kode.xlsx`.
Če ta datoteka ne obstaja, jo funkcija `povezi_z_wsm`
samodejno napolni z najpogostejšimi izrazi iz `*_povezane.xlsx`.

Za artikle, kjer masa na kos ni razvidna iz naziva, preverite slovar
`WEIGHTS_PER_PIECE` v `wsm/constants.py`. Če naletite na novo kodo s
stalno maso pakiranja, jo dodajte v ta slovar.

6. Za spremljanje cen že povezanih artiklov odprite vmesnik **Price Watch**.
   V glavnem meniju (`python -m wsm.run`) je na voljo gumb "Spremljaj cene",
   vmesnik pa lahko po potrebi zaženete tudi neposredno s funkcijo
   `launch_price_watch`.

   V zgornjem delu okna je iskalnik za hitro filtriranje dobaviteljev. Spodaj
   se nahaja dodatno polje za iskanje po nazivih artiklov. Rezultati so
   prikazani v tabeli s stolpci "Artikel", "Zadnja cena", "Zadnji datum",
   "Min" in "Max". Po posameznem stolpcu lahko razvrstite s klikom na glavo.
   Dvojni klik na vrstico odpre graf gibanja cen iz `price_history.xlsx`.

7. Analizo in združevanje postavk lahko izvedete z:
   ```bash
   python -m wsm.cli analyze <invoice.xml> --suppliers links
   ```
   Ukaz izpiše povzetek po WSM šifrah in preveri, ali se vsota ujema z
   vrednostjo na računu.

### Poganjanje testov

Pri poganjanju testov se naložijo tudi moduli, ki niso del samega paketa.
Zato je treba namestiti razvojne odvisnosti, sicer lahko `pytest` med
zbiranjem testov javi napake. Med pomembnejšimi paketih so
`pandas`, `openpyxl`, `pdfplumber`, `pytest`, `pytest-cov` in `matplotlib`.
Najlažje jih dodate z namestitvijo datoteke `requirements-dev.txt`:

```bash
pip install -r requirements-dev.txt
pytest
```

Brez teh paketov se testi ne bodo pravilno zagnali.

## Možne izboljšave

- **Izboljšano samodejno povezovanje**: poleg iskanja po ključnih besedah bi lahko uporabili knjižnice za "bližnje ujemanje" (npr. `rapidfuzz`), ali pa vektorsko iskanje, kot nakazuje mapa `wsm_vector_embedding`. Tako bi sistem lažje našel ustrezno WSM šifro tudi pri rahlo različnih nazivih artiklov.
- **Bolj zmogljiv GUI**: Tkinter je enostaven, a za obsežnejše tabele bi lahko razmislili o prehodu na `PyQt5`, ki omogoča naprednejše filtriranje in iskanje.
- **Testne enote za GUI**: poleg obstoječih testov za parsanje XML je smiselno dodati teste, ki preverijo logiko povezovanja (npr. ali `_save_and_close` pravilno posodobi Excel). Osnovne funkcije GUI se da avtomatizirati z unit testi.


## Licenca
Ta projekt uporablja licenco MIT. Celotno besedilo najdete v datoteki [LICENSE](LICENSE).

