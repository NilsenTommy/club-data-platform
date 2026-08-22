# Arkitektur

Teknisk dybdedokumentasjon for [Football Club Data Platform](../README.md).
Dokumentet beskriver lagdelingen, hvert pipeline-steg, datasettene og de
konkrete avveiingene som ligger bak. README-en forteller *hvorfor* prosjektet
finnes — dette dokumentet forteller *hvordan* det er bygget.

> Dette er en prototype. Beslutningene under er tatt for å demonstrere prinsipper
> med minst mulig verktøykjede, ikke for å tåle produksjonslast.

## Innhold

1. [Designprinsipper](#designprinsipper)
2. [Lagdeling](#lagdeling)
3. [Pipelinesteg](#pipelinesteg)
4. [Silver-datasett](#silver-datasett)
5. [Gold-datasett](#gold-datasett)
6. [Determinisme og datakvalitet](#determinisme-og-datakvalitet)
7. [Avveiinger](#avveiinger)
8. [Kjente begrensninger](#kjente-begrensninger)
9. [Veikart](#veikart)

---

## Designprinsipper

| Prinsipp | Hva det betyr i koden |
|---|---|
| **Rådata er hellige** | HTTP-responser lagres byte-for-byte i Bronze. Ingen flattening, ingen forretningslogikk, ingen overskriving. |
| **Kildeuavhengig modell** | Silver definerer plattformens egne entiteter. Downstream-kode kjenner ikke til FootballData, Nominatim eller Frost. |
| **Ett lag om gangen** | Gold leser bare Silver. Silver leser bare Bronze. Ingen steg hopper over et lag. |
| **Determinisme foran magi** | Alle tvetydigheter løses av eksplisitte, sorterte tiebreakere. Samme input gir byte-identisk output. |
| **Feil skal stoppe bygget** | Validering er eksplisitt Python, ikke et rammeverk. Kritiske brudd kaster en typet exception med tydelig melding. |
| **Konservativ heller enn imponerende** | Der prototypen ikke kan avgjøre noe trygt (identitet, samtykke, geokoding), lar den saken være uløst i stedet for å gjette. |

## Lagdeling

### Bronze — kildesystemenes sannhet

Bronze representerer kildesystemene slik de faktisk svarte. Filene er
immutable og fungerer samtidig som cache, slik at pipelinen kan bygges på nytt
uten å belaste eksterne API-er.

```text
data/bronze/
├── football/                  FootballData-responser per hentedato
├── geocoding/                 Nominatim-responser per venue-query
├── weather/
│   ├── sources/               Frost station lookup per venue og dato
│   └── observations/          Frost observasjoner per stasjon og tidspunkt
└── supporter/
    ├── ticket_system/         Simulert billettsystem (CSV)
    └── app/                   Simulert supporterapp (CSV)
```

Filnavn inneholder en hash av den effektive forespørselen. Endrer spørringen seg,
skrives en ny fil i stedet for at en gammel respons muteres. Det gjør det mulig å
inspisere originaldata, bygge Silver på nytt og endre intern modell uten å hente
alle kildene på nytt.

### Silver — plattformens interne modell

Rå JSON og CSV blir typet, deduplisert, validert og skrevet som Parquet.

```text
data/silver/
├── matches.parquet
├── venues.parquet
├── weather_observations.parquet
├── silver_fans.parquet
├── silver_fan_identities.parquet
└── silver_ticket_sales.parquet
```

Silver innfører plattformens egne nøkler — `venue_id`, `fan_id`, `match_id` —
slik at koblinger skjer på eksplisitte identifikatorer i stedet for på fritekst
som stadionnavn eller e-postadresser.

### Gold — dataprodukter med tydelig grain

Gold bygges utelukkende fra Silver og gjør ingen API-kall.

```text
data/gold/
├── match_insights.parquet     én rad per kamp
└── fan_activation.parquet     én rad per canonical fan
```

Hvert produkt har én definert konsumentsituasjon og ett grain. Produkter som
ikke har en identifisert bruker, er bevisst ikke bygget.

## Pipelinesteg

Kommandoene kjøres fra repo-roten i denne rekkefølgen:

```bash
python3 -m src.fetch_matches
python3 -m src.geocode_venues
python3 -m src.fetch_weather
python3 -m src.build_silver
python3 -m src.build_gold
python3 -m src.generate_fan_data
python3 -m src.build_fan_silver
python3 -m src.build_fan_gold --as-of 2026-08-22
```

### 1. Hent kamper — `src/fetch_matches.py`

Henter kamper for det hardkodede laget `293` og lagrer hele responsen under
`data/bronze/football/matches_YYYY-MM-DD.json`.

Responsen er et objekt med `success`, `data` og `meta`; kamplisten ligger på
`data.matches`. Skriptet bruker 30 sekunders timeout og gir tydelige feil for
manglende nøkkel, autentiseringsfeil, nettverksfeil og andre HTTP-statuser enn
200.

### 2. Geocode stadioner — `src/geocode_venues.py`

Leser nyeste kampfil, dedupliserer søk og kaller Nominatim sekvensielt med minst
ett sekund mellom faktiske kall. Eksisterende filer brukes som immutable cache.

```text
data/bronze/geocoding/venue_<slug>_<query-hash>.json
```

**Avveiing:** en tidlig variant søkte på full gateadresse pluss hjemmelag og
traff bare 1 av 11 stadioner. Den implementerte spørringen bruker stadionnavn +
siste kommaseparerte lokasjonsledd som by-hint + konkret land (aldri `Europe`),
og treffer 9 av 11. Cache-nøkkelen inkluderer den effektive spørringen, så gamle
responser forblir immutable når spørringslogikken endres.

Første Nominatim-resultat brukes. Tomme søk lagres og rapporteres, men stopper
ikke pipelinen.

### 3. Hent historisk vær — `src/fetch_weather.py`

Bruker venue-koordinatene til å finne nærmeste Frost-stasjon som tilbyr
`air_temperature`, `sum(precipitation_amount PT1H)` og `wind_speed`.

- Stasjonen aksepteres bare når avstanden er **≤ 50 km** fra stadion.
- Observasjoner hentes fra tre timer før til tre timer etter avspark.
- Source- og observation-responser lagres separat i `sources/` og `observations/`.

En observasjonsrespons regnes som `available` bare når unionen av
`data[].observations[].elementId` dekker alle tre elementene. Ufullstendige, men
ikke-tomme responser beholdes som rå Bronze og klassifiseres som `partial`.
Manglende dekning er et forventet utfall for enkelte europeiske stadioner.

**Hvorfor Frost og ikke Locationforecast:** Locationforecast leverer prognoser
omtrent ni dager fram i tid. Prosjektet trenger observasjoner for ferdigspilte
kamper.

### 4. Bygg Silver — `src/build_silver.py`

Leser dagens Bronze-filer, bygger canonical entiteter, validerer dem og skriver
tre Parquet-filer.

```text
Matches:              21 rows
Venues:                9 rows, 7 geocoded
Weather observations: 657 rows, 3 elements
```

Tallene er et øyeblikksbilde fra datasettet i repoet og endres når nye
Bronze-data hentes.

### 5. Bygg Gold — `src/build_gold.py`

Leser de tre Silver-filene, velger været ved kampstart, validerer resultatet og
skriver `data/gold/match_insights.parquet`. Steget gjør ingen API-kall og leser
aldri Bronze direkte.

```text
Match insights:        21 rows
Med venue-koordinater: 14 rows
Med vær ved avspark:    8 rows
```

### 6. Generer syntetiske supporterdata — `src/generate_fan_data.py`

Leser kampene fra Silver og genererer to separate, simulerte kildesystemer
direkte i Bronze:

```text
data/bronze/supporter/
├── ticket_system/
│   ├── ticket_customers.csv    kunde-ID, kontaktfelt, marketing_consent, consent_updated_at
│   └── ticket_sales.csv        kjøp knyttet til match_id i Silver
└── app/
    └── app_users.csv           separat bruker-ID, profilnavn, appinnstillinger
```

Generatoren lager 500 underliggende supportere: 425 billettkunder, 375
appbrukere og 300 personer som finnes i begge systemene.

**Det viktigste designvalget:** den interne ground truth-identiteten brukes bare
mens dataene genereres og skrives aldri til råfilene. Fan-Silver må derfor løse
koblingen fra fragmenterte kildefelter på samme måte som mot reelle
kildesystemer — det finnes ingen fasit å jukse med.

Fragmenteringen er bevisst realistisk. For overlappende personer inneholder
dataene eksakte e-poster, forskjeller i store/små bokstaver, ekstra mellomrom,
`+alias`, alternative syntetiske adresser og manglende adresser. Navn varierer
mellom fullt navn, fornavn, initialer og kallenavn. Det gir både enkle
normaliseringsproblemer og genuint tvetydige tilfeller.

Etterspørselen varierer syntetisk med hjemme-/bortekamp, turnering, motstander,
ukedag og tidligere kampresultater. Bare resultater fra kamper som allerede er
spilt, brukes. Når `gold/match_insights.parquet` finnes, brukes kampværet til å
skape moderat færre sene kjøp ved dårlig vær. Dette er en mekanisme for å få et
meningsfullt demonstrasjonsdatasett, ikke produksjonslineage fra Gold til Bronze.

### 7. Bygg canonical fan-Silver — `src/build_fan_silver.py`

Leser de tre supporterfilene i Bronze og skriver `silver_fans.parquet`,
`silver_fan_identities.parquet` og `silver_ticket_sales.parquet`.

Identitetskoblingen er bevisst enkel og konservativ:

1. E-post normaliseres med trim, små bokstaver og fjerning av `+alias`.
2. En ticket-identitet og en app-identitet kobles **bare** når den normaliserte
   adressen forekommer nøyaktig én gang i hver kilde.
3. Duplikater, manglende e-post og alternative adresser forblir separate fans.

Det gir bevisst et konservativt resultat i stedet for en falsk sikkerhet om at
prototypen løser alle identitetsproblemer.

```text
Fans:              540 rows, 260 linked across sources
Fan identities:    800 rows
Ticket sales:     3771 rows
Marketing consent: 283 true, 142 false, 115 unknown
Activation eligible: 278 rows
```

Ticketing er autoritativ kilde for `marketing_consent`. App-only og uløste
appidentiteter får **ukjent** samtykke, ikke et implisitt avslag.

### 8. Bygg fan activation-Gold — `src/build_fan_gold.py`

Leser canonical fans og billettsalg fra Silver og skriver
`data/gold/fan_activation.parquet`. En eksplisitt `--as-of`-dato gjør
12-månedersvinduet og outputen reproduserbar.

```text
Fans:              540 rows
Marketing allowed: 278 rows
Segments:          115 INACTIVE, 13 OCCASIONAL,
                   206 ENGAGED, 206 HIGHLY_ENGAGED
```

## Silver-datasett

### `matches.parquet`

Én rad per logical fixture med kamp-ID, UTC-avspark, turnering, sesong, lag,
score, status, `venue_id` og rå venue-felter.

Dupliserte source-records identifiseres med kombinasjonen avspark + turnering +
sesong + hjemmelag-ID + bortelag-ID. Vinneren velges deterministisk: flest
utfylte felter, deretter completed status, deretter høyeste source match ID. I
dagens datasett beholdes 200399 og 200364, mens de dårligere duplikatene 197228
og 197235 forkastes.

`venue_id` beregnes med samme venue-resolution som `venues.parquet`, slik at
downstream kobler på en eksplisitt nøkkel i stedet for stadionnavn. Feltet er
nullable — 5 av 21 kamper mangler venue-felter i kilden. `attendance` er
nullable fordi kilderesponsen ikke inneholder feltet.

### `venues.parquet`

Én rad per canonical stadion med stabil `venue_id`, rå stadiontekst, koordinater
og tilgjengelig Nominatim-metadata.

`venue_id` er `VENUE-<16 hex>` fra SHA-256 med denne prioriterte identiteten:

1. Nominatim OSM-type og OSM-ID når venue er geocodet.
2. Avrundede koordinater når OSM-ID mangler.
3. Normalisert stadionnavn og lokasjon når venue ikke er geocodet.

Dette samler blant annet flere tekstvarianter av Aspmyra til én venue via OSM
way `24292284`. `geocoding_confidence` er nullable fordi Nominatim ikke leverer
en score som tilsvarer feltet.

### `weather_observations.parquet`

Historiske observasjoner i long format: én canonical måling per venue,
værstasjon, tidspunkt og element, med station metadata og Haversine-avstand
mellom stadion og stasjon.

Frost kan returnere flere tidsserier for samme tidspunkt og element. Silver
velger deterministisk én serie:

1. Laveste `qualityCode`.
2. `PT1H` før `PT30M` før `PT10M`.
3. Laveste `timeSeriesId`.
4. Første forekomst i kilderesponsen.

Regelen er en prototypebeslutning, ikke en universell meteorologisk regel.

### `silver_fans.parquet`

Én rad per canonical fan med stabil `fan_id`, foretrukket normalisert e-post,
visningsnavn, første observerte tidspunkt og antall koblede kildesystemer.

`fan_id` er en deterministisk hash av ticket-identiteten når den finnes, ellers
app-identiteten. Nye kilderader som sorterer tidligere endrer dermed ikke
eksisterende fan-ID-er.

`marketing_consent` og `consent_updated_at` følger ticket-identiteten inn i den
canonical fanen. `activation_eligible` krever både `marketing_consent = True` og
en kontaktbar normalisert e-post. Feltet demonstrerer hvordan identitet og
samtykke kombineres med atferd — det er ikke en produksjonsklar policy engine.

### `silver_fan_identities.parquet`

Bridge-tabell mellom `fan_id` og kildenes egne identifikatorer. `source` er
`ticketing` eller `app`, `source_id` beholder original kunde- eller
appbruker-ID, og `match_method` viser om identiteten ble koblet via normalisert
e-post eller bare representerer én kilde.

Bridge-modellen gjør det mulig å legge til flere kilder — for eksempel en
commerce-identitet — uten å endre grain i `silver_fans.parquet`.

### `silver_ticket_sales.parquet`

Typet billettsalg med både `fan_id`, original `ticket_customer_id` for lineage og
`match_id` for kobling til kampdata. Alle salg må ha en gyldig fan og kamp.

## Gold-datasett

### `match_insights.parquet`

| Felt | Beskrivelse |
|---|---|
| `match_id`, `kickoff_at` | Kampnøkkel og UTC-avspark |
| `competition`, `season` | Turnering og sesong |
| `home_team_name`, `away_team_name` | Lag |
| `home_score`, `away_score` | Sluttresultat, nullable |
| `result` | `win`, `draw`, `loss` eller null |
| `venue_id`, `stadium_name`, `country` | Stadion, nullable |
| `latitude`, `longitude` | Stadionkoordinater, nullable |
| `weather_observed_at` | Valgt observasjonstidspunkt, nullable |
| `temperature_c`, `precipitation_mm`, `wind_speed_ms` | Vær ved kampstart, nullable |

`result` beregnes for team ID `293` og settes bare når kampen har status
`complete`/`finished` og begge scorer finnes.

Vær velges deterministisk per kamp:

1. Kandidatene må høre til kampens `venue_id`.
2. Observasjonen må være maksimalt tre timer fra avspark.
3. Korteste absolutte tidsavstand vinner.
4. Ved lik avstand foretrekkes tidspunktet før avspark.
5. Avstand til værstasjon og stasjons-ID er siste tiebreakere.

Deretter pivoteres de tre elementene fra det valgte tidspunktet til hver sin
kolonne. Alle koblinger er venstre joins, slik at kamper uten geokoding eller vær
fortsatt er med med nullable felter. Joinene bruker pandas `validate="many_to_one"`
mot venues og `"one_to_one"` mot vær; en `MergeError` kastes videre som
`GoldBuildError`.

### `fan_activation.parquet`

Én rad per canonical fan, også for fans uten kjøp eller marketing-tillatelse.

| Felt | Beskrivelse |
|---|---|
| `fan_id`, `primary_email`, `display_name` | Canonical fan og kontaktfelt |
| `as_of_at`, `window_start_at` | Eksklusiv snapshot-grense og inklusiv start på 12-månedersvinduet |
| `matches_purchased_12m` | Distinkte kamper med completed kjøp |
| `purchase_transactions_12m` | Antall completed kjøpstransaksjoner |
| `tickets_purchased_12m` | Sum `quantity` for completed kjøp |
| `total_spend_12m` | Sum `quantity * unit_price_nok` for completed kjøp |
| `last_engagement_date` | Siste completed `purchased_at` før snapshot, all-time |
| `cancelled_transactions_12m`, `refunded_transactions_12m` | Friksjonssignaler, inngår ikke i spend eller segment |
| `engagement_segment` | `INACTIVE`, `OCCASIONAL`, `ENGAGED`, `HIGHLY_ENGAGED` |
| `marketing_consent`, `consent_updated_at` | Samtykkesnapshot fra ticketing |
| `marketing_allowed` | Sant bare ved eksplisitt samtykke og kontaktbar e-post |

Segmentet bygger på `matches_purchased_12m`: 0 gir `INACTIVE`, 1–2 gir
`OCCASIONAL`, 3–5 gir `ENGAGED`, 6 eller flere gir `HIGHLY_ENGAGED`. Vinduet
bruker `purchased_at` i intervallet `[window_start_at, as_of_at)`.

Tre presiseringer som er lette å ta feil av:

- `matches_purchased_12m` er **ikke** attendance. Plattformen har ingen
  billettscan- eller eventkilde som kan bekrefte oppmøte.
- `push_opt_in` er en kanalpreferanse, **ikke** marketing consent.
- Silver har bare siste consent-snapshot. Et bygg avvises derfor hvis
  `consent_updated_at` ligger etter valgt `as_of_at`.

## Determinisme og datakvalitet

### Determinisme

Uendrede Bronze-filer gir byte-identiske Silver-filer ved gjentatt kjøring i
samme miljø. Det samme gjelder Gold for uendret Silver. Determinismen er testet,
ikke bare påstått.

Det er dette som gjør resten mulig: uten stabil output kan man verken
sammenligne bygg, revidere en beslutning eller stole på at en endring i logikk
faktisk er årsaken til en endring i tall.

### Validering

Bygget stopper med en typet exception og tydelig melding ved kritiske feil:

**Silver**

- manglende eller duplisert `match_id`
- manglende avsparktid eller lagnavn
- ugyldige bredde- eller lengdegrader
- ugyldig Frost- eller Nominatim-JSON
- manglende observation-tid eller element
- weather values som ikke er numeriske

**Gold**

- `match_id` og `kickoff_at` kan ikke være null, `match_id` må være unik
- Gold må ha nøyaktig samme kamper som Silver
- joins kan ikke duplisere kamper
- valgt vær kan ikke ligge mer enn tre timer fra avspark
- `result` kan bare være `win`, `draw`, `loss` eller null
- fan activation må ha nøyaktig samme fans som fan-Silver
- kjøpsmål kan ikke være negative eller ikke-endelige
- segment og `marketing_allowed` må stemme med beregningsreglene
- consent-snapshot kan ikke ligge etter valgt `as_of`

### Tester

98 tester kjører uten live API-kall, med mocks og midlertidige mapper:

```bash
python3 -m unittest discover -s tests -v
```

De dekker HTTP-feil, credentials, caching, rå byte-lagring, deduplisering,
canonical venue-ID-er, weather-serievalg, valg av vær ved kampstart,
resultatlogikk, supporterfragmentering, canonical fan-kobling, deterministiske
CSV- og Parquet-filer, datatyper, fan-segmentering, consent-aware aktivering,
validering og Parquet-skriving.

## Avveiinger

| Valg | Alternativ | Hvorfor dette valget |
|---|---|---|
| Filbasert Bronze med hash i filnavn | Database eller objektlagring med versjonering | Gjør rådata inspiserbare i en editor og gir gratis idempotens. Objektlagring er neste steg når volumet vokser. |
| Parquet + pandas | DuckDB, Spark, dbt | Datasettet er lite. Å innføre en motor uten et problem å løse ville skjult prinsippene bak verktøy. |
| Eksplisitt validering i Python | Great Expectations, Pandera | Får fram *hva* som valideres og hvorfor, uten et rammeverk å lære seg først. Et rammeverk lønner seg når reglene deles på tvers av team. |
| Full refresh av Silver og Gold | Inkrementell load | Full refresh er trivielt reproduserbart. Inkrementell load krever watermark, sen ankomst og korreksjonshåndtering — reell kompleksitet uten reell gevinst her. |
| Deterministisk regelbasert identitetskobling | Probabilistisk matching / ML | En konservativ regel er forklarbar og reviderbar. Et sannsynlighetsscore uten fasit ville gitt tall ingen kan etterprøve. |
| `--as-of` som påkrevd parameter i fan-Gold | `datetime.now()` | Gjør 12-månedersvinduet reproduserbart. Et bygg som avhenger av klokka kan ikke testes deterministisk. |
| Ukjent samtykke som egen tilstand | Behandle manglende samtykke som `False` | `False` er en aktiv avvisning fra en person. `Unknown` betyr at plattformen ikke vet. Å slå dem sammen ville skjult et reelt datakvalitetsproblem. |
| Ingen ground truth i syntetisk Bronze | Skrive intern person-ID til råfilene | Tvinger fram at identitetskoblingen faktisk må løses, i stedet for å demonstrere en join på en nøkkel som ikke finnes i virkeligheten. |
| 50 km-grense mot værstasjon | Nærmeste stasjon uansett avstand | En stasjon 200 km unna beskriver ikke været på stadion. Bedre med manglende data enn misvisende data. |

## Kjente begrensninger

Bevisste prototypebegrensninger i dagens kode:

- Kun ett hardkodet lag (`293`).
- Filbasert lagring og caching, ingen objektlagring eller katalog.
- Ingen generell konfigurasjon av tidsvinduer eller datakilder.
- Ingen retries, orkestrering eller distribuert behandling.
- Første geocoding-resultat velges automatisk, uten manuell kvalitetssikring.
- Silver og Gold bygges som full refresh.
- Identitetskoblingen bruker bare unik normalisert e-post, og håndterer ikke
  probabilistisk matching, manuell overstyring eller historiske identiteter.
- Fan activation bruker kjøp som engagement-signal fordi faktisk attendance og
  app-events ikke finnes i kildedataene.
- Consent er et siste snapshot, ikke en historisert event- eller SCD-modell.
- Gold har ingen prediktiv aktiveringsscore eller kamprettet fan-match-modell.

Se også [governance.md](governance.md) for gap knyttet til eierskap, persondata
og samtykke.
