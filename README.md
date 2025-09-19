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
  Med glavnimi odvisnostmi so `pandas`, `pdfplumber`, `openpyxl` in `lxml`.
   Za grafični vmesnik **Price Watch** potrebujete tudi `matplotlib` in `mplcursors`:
   ```bash
   pip install 'wsm[plot]'
   ```
   Za PyQt različico GUI lahko namestite tudi `PyQt5` preko
   ```bash
   pip install 'wsm[pyqt]'
   ```
  Za razvoj in poganjanje testov so potrebni tudi `pytest`, `pandas` in
  `lxml`. Vse naštete pakete namestite z:
   ```bash
   pip install -r requirements-dev.txt
   ```
   Namesto zgornjih ukazov lahko uporabite skripto, ki naloži vse pakete:
   ```bash
  ./scripts/setup_env.sh
  ```

## Development setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q        # run test-suite
pre-commit run -a  # lint & format
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
4. Nastavite privzete poti z okoljskimi spremenljivkami:
   ```bash
   export WSM_LINKS_DIR=links
   export WSM_CODES_FILE=sifre_wsm.xlsx
   export WSM_KEYWORDS_FILE=kljucne_besede_wsm_kode.xlsx
   ```
   S tem lahko ukaze zaganjate brez dodatnih parametrov.

## Dinamično zaokroževanje

- `WSM_TOLERANCE_BASE` – osnovna toleranca za primerjavo postavk (privzeto `0.02`).
- `WSM_MAX_TOLERANCE` – zgornja meja samodejne tolerance (privzeto `0.50`).
- `WSM_SMART_TOLERANCE` – vklopi pametno prilagajanje tolerance glede na znesek računa (privzeto `true`).
- `WSM_AUTO_ROUNDING` – vklopi dodajanje samodejne korekcijske vrstice, ko razlika preseže toleranco (privzeto `false`).

  **Primeri uporabe:**

  ```bash
  export WSM_AUTO_ROUNDING=1      # vklopi samodejno korekcijo
  export WSM_SMART_TOLERANCE=1    # prilagodi toleranco glede na velikost računa
  export WSM_TOLERANCE_BASE=0.05  # nastavi osnovno toleranco na 5 centov
  ```


5. Za osnovno validacijo računov lahko zaženete:
   ```bash
   python -m wsm.cli validate <mapa_z_racuni>
   ```
   kjer `<mapa_z_racuni>` vsebuje XML ali PDF datoteke z e‑računi.

6. Za ročno povezovanje WSM šifer podajte pot do računa:
   ```bash
   WSM_LINKS_DIR=links \
   WSM_CODES_FILE=sifre_wsm.xlsx \
   WSM_KEYWORDS_FILE=kljucne_besede_wsm_kode.xlsx \
   python -m wsm.cli review <invoice.xml>
   ```
  (po želji dodajte `--price-warn-pct <odstotek>` ali `--use-pyqt` za Qt različico)
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
  Vrstico lahko uredite z Enterjem ali z dvojnim klikom in tako spremenite ime v stolpcu "WSM".
  Vrednost potrdite s tipko Enter, s tipko Backspace jo lahko tudi odstranite.
  Račun shranite z gumbom "Shrani & zapri" ali s bližnjico F10.
  Pod tabelo se izpiše povzetek s skupnimi zneski.
  Če se del okna ne vidi, ga razširite s tipko F11.
  Ob potrditvi vrstice program primerja ceno s prejšnjimi zapisi v
  `price_history.xlsx`. Če je odstopanje večje od nastavljenega praga
  (privzeto 1&nbsp;% oz. vrednost spremenljivke `WSM_PRICE_WARN_PCT`), se
  vrstica obarva oranžno in prikaže se namig z zadnjo ceno ter razliko v
  odstotkih.
  Parameter `--price-warn-pct` omogoča začasno nastavitev drugega praga.



Če `--wsm-codes` ni podan, program poskuša prebrati `sifre_wsm.xlsx` v
korenu projekta.
Lahko pa pot do datoteke določite tudi z okoljsko spremenljivko
`WSM_CODES_FILE`. Podobno lahko z `WSM_LINKS_DIR` nastavite mapo s povezavami
do dobaviteljev. GUI in ukazi CLI privzeto upoštevajo ti spremenljivki,
če argumenti niso podani.

Za datoteko s ključnimi besedami lahko nastavite okoljsko spremenljivko
`WSM_KEYWORDS_FILE`, ki kaže na `kljucne_besede_wsm_kode.xlsx`. Če ni
nastavljena, program privzeto bere to datoteko iz trenutne mape.


Pri samodejnem povezovanju lahko program iz teh ročno
shranjenih datotek sam izdela datoteko `kljucne_besede_wsm_kode.xlsx`.
Če ta datoteka ne obstaja, jo funkcija `povezi_z_wsm`
samodejno napolni z najpogostejšimi izrazi iz `*_povezane.xlsx`.

Za artikle, kjer masa na kos ni razvidna iz naziva, preverite slovar
`WEIGHTS_PER_PIECE` v `wsm/constants.py`. Če naletite na novo kodo s
stalno maso pakiranja, jo dodajte v ta slovar.

7. Za spremljanje cen že povezanih artiklov odprite vmesnik **Price Watch**.
   V glavnem meniju (`python -m wsm.run`) je na voljo gumb "Spremljaj cene",
   vmesnik pa lahko po potrebi zaženete tudi neposredno s funkcijo
   `launch_price_watch`.

   V zgornjem delu okna je iskalnik za hitro filtriranje dobaviteljev. Ob njem
   je še polje za izbiro števila tednov (privzeto 30) in koledar za izbiro
   začetnega datuma. Če je nameščen paket `tkcalendar`, se privzeta vrednost
   koledarja nastavi na prvi dan trenutnega leta. Spodaj se nahaja dodatno
   polje za iskanje po nazivih artiklov. Tabela se ob spremembi
   števila tednov ali datuma osveži samodejno, filter pa lahko po želji potrdite tudi
   z gumbom "Potrdi". Rezultati so
   prikazani v tabeli s stolpci "Artikel", "Neto cena", "€/kg|€/L",
   "Zadnji datum", "Min" in "Max". Po posameznem stolpcu lahko razvrstite
   s klikom na glavo.
   Dvojni klik na vrstico odpre graf gibanja cen iz `price_history.xlsx`.
   Zgodovina se beleži v **neto** vrednostih brez DDV. Pri artiklih, kjer je
   enota `kg` ali `L`, se cena shrani tudi kot cena na kilogram oziroma liter
   in je prikazana v stolpcu "€/kg|€/L". Graf pri dvokliku uporabi to
   vrednost, če je na voljo.

8. Analizo in združevanje postavk lahko izvedete z:
   ```bash
   WSM_LINKS_DIR=links python -m wsm.cli analyze <invoice.xml>
   ```
Ukaz izpiše povzetek po WSM šifrah in preveri, ali se vsota ujema z
vrednostjo na računu.

### Line discounts

The invoice parser distinguishes between *informational* and *real* line
discounts.  Line level discounts in ``SG39`` are **ignored** when all of the
following hold:

* The header net total (``MOA 125`` or ``389``) equals the sum of ``MOA 203``
  amounts across all lines.
* That header total differs from the net amount calculated after applying line
  discounts.
* The document does **not** contain a ``MOA 260`` element.

In this situation, allowance/charge segments are treated as annotations only
and do not affect totals.  Example (discount ignored):

```xml
<G_SG26>
  <S_MOA><C_C516><D_5025>203</D_5025><D_5004>8</D_5004></C_C516></S_MOA>
  <G_SG39>
    <S_ALC><D_5463>A</D_5463></S_ALC>
    <G_SG42>
      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>
    </G_SG42>
  </G_SG39>
</G_SG26>
<G_SG50>
  <S_MOA><C_C516><D_5025>389</D_5025><D_5004>8</D_5004></C_C516></S_MOA>
</G_SG50>
```

Expected result: the line discount is ignored, ``discount_total`` is ``0`` and
the net total remains ``8``.

Line discounts are **applied** whenever one of the above conditions is not
met—either the header total differs from the ``MOA 203`` sum or a ``MOA 260``
segment is present.  Example (discount applied):

```xml
<G_SG26>
  <S_MOA><C_C516><D_5025>203</D_5025><D_5004>10</D_5004></C_C516></S_MOA>
  <G_SG39>
    <S_ALC><D_5463>A</D_5463></S_ALC>
    <G_SG42>
      <S_MOA><C_C516><D_5025>204</D_5025><D_5004>2</D_5004></C_C516></S_MOA>
    </G_SG42>
  </G_SG39>
</G_SG26>
<G_SG50>
  <S_MOA><C_C516><D_5025>389</D_5025><D_5004>8</D_5004></C_C516></S_MOA>
</G_SG50>
```

Expected result: the parser subtracts the discount and reports a
``discount_total`` of ``2`` with a net total of ``8``.  Adding a ``MOA 260``
segment in the header would also force this behaviour even if the header net
matched the sum of ``MOA 203`` amounts.

### Okoljske spremenljivke

| Ime | Privzeto | Opis |
| --- | --- | --- |
| `WSM_LINKS_DIR` | `links` | Mapa, kjer se shranjujejo ročno povezani računi. |
| `WSM_CODES_FILE` | `sifre_wsm.xlsx` | Excel s šiframi WSM, ki jih uporablja program. |
| `WSM_KEYWORDS_FILE` | `kljucne_besede_wsm_kode.xlsx` | Datoteka s ključnimi besedami za hitrejše iskanje ustreznih šifer. |
| `AVG_COST_SKIP_ZERO` | `0` | Če je nastavljena na `1`, funkcija `wsm.utils.average_cost` pri izračunu povprečne cene preskoči postavke z ničelno ceno. |

### Namestitev razvojnih odvisnosti

Pred zagonom testov namestite pakete iz `requirements-dev.txt`. Datoteka že
vključuje vse odvisnosti iz `requirements.txt`:

```bash
pip install -r requirements-dev.txt
```

### Poganjanje testov

Pred zagonom `pytest` je **obvezno**, da namestite pakete iz
`requirements-dev.txt`. Uporabite naslednje ukaze:

```bash
pip install -r requirements-dev.txt
pytest -q
```

`requirements-dev.txt` samodejno povleče vse pakete iz `requirements.txt` in
vključuje tudi `matplotlib` ter `mplcursors`, ki sta potrebna za teste
**Price Watch**. Brez teh paketov se testi ne bodo pravilno zagnali. Namesto
ročnega izvajanja lahko uporabite skripto `./scripts/setup_env.sh`.

### Linting in formatiranje

Projekt uporablja `black` in `flake8` za poenotenje kode. Nameščena sta v `requirements-dev.txt`.

```bash
pip install -r requirements-dev.txt
pre-commit install  # opcijsko
```

Če je nastavljen `pre-commit`, se oba orodja zaženeta ob vsakem commitu.

## Možne izboljšave

- **Izboljšano samodejno povezovanje**: poleg iskanja po ključnih besedah bi lahko uporabili knjižnice za "bližnje ujemanje" (npr. `rapidfuzz`), ali pa vektorsko iskanje, kot nakazuje mapa `wsm_vector_embedding`. Tako bi sistem lažje našel ustrezno WSM šifro tudi pri rahlo različnih nazivih artiklov.
- **Bolj zmogljiv GUI**: Tkinter je enostaven, a za obsežnejše tabele bi lahko razmislili o prehodu na `PyQt5`, ki omogoča naprednejše filtriranje in iskanje.
- **Testne enote za GUI**: poleg obstoječih testov za parsanje XML je smiselno dodati teste, ki preverijo logiko povezovanja (npr. ali `_save_and_close` pravilno posodobi Excel). Osnovne funkcije GUI se da avtomatizirati z unit testi.

## Varnost

Pri odpiranju XML datotek se uporablja `lxml` z onemogočenim
reševanjem zunanjih entitet (`resolve_entities=False`). S tem se
izognemo branju datotek ali nalaganju vsebine preko zunanjih entitet
(XXE napadi).


## Licenca
Ta projekt uporablja licenco MIT. Celotno besedilo najdete v datoteki [LICENSE](LICENSE).

