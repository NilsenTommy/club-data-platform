# Governance

Governance-dokumentasjon for [Football Club Data Platform](../README.md).
Dokumentet beskriver hvem som eier hva, hvordan persondata og samtykke er
håndtert i prototypen, hvilke gap som finnes, og hva som måtte på plass før noe
liknende kunne kjøre i en faktisk klubb.

> [!IMPORTANT]
> Dette er en demonstrasjon. All supporterdata er syntetisk. Dokumentet beskriver
> hvordan prototypen er bygget og hvilke prinsipper den forsøker å vise — det er
> ikke en juridisk vurdering og erstatter ikke en reell personvernvurdering.

## Innhold

1. [Prinsipper](#prinsipper)
2. [Eierskap og ansvar](#eierskap-og-ansvar)
3. [Persondata og dataminimering](#persondata-og-dataminimering)
4. [Samtykke og aktivering](#samtykke-og-aktivering)
5. [Syntetiske data](#syntetiske-data)
6. [Datakvalitet som kontrakt](#datakvalitet-som-kontrakt)
7. [Kjente gap og plan](#kjente-gap-og-plan)
8. [Lisenser og attribution](#lisenser-og-attribution)

---

## Prinsipper

```text
Kildesystemene eier de operasjonelle prosessene.
Domenene beholder forretningseierskapet.
Plattformen lager gjenbrukbare canonical data.
Persondata skilles fra analytisk bruk der det er mulig.
Aktivering krever gyldig samtykke.
```

Prinsippene er ikke pyntetekst — hver av dem har en konkret konsekvens i koden:

| Prinsipp | Konsekvens i implementasjonen |
|---|---|
| Kildesystemene eier de operasjonelle prosessene | Plattformen skriver aldri tilbake til en kilde. Bronze lagrer responsen som mottatt og endrer ingenting. |
| Domenene beholder forretningseierskapet | Ticketing er autoritativ for samtykke. Plattformen kopierer verdien, den definerer den ikke. |
| Plattformen lager gjenbrukbare canonical data | Silver innfører `venue_id`, `fan_id` og `match_id` slik at konsumenter slipper å kjenne kildeskjemaene. |
| Persondata skilles fra analytisk bruk der det er mulig | Kampdomenet inneholder ingen persondata. Kontaktfelter finnes bare der aktivering faktisk krever dem. |
| Aktivering krever gyldig samtykke | `marketing_allowed` er en eksplisitt, validert kolonne, og segmentering alene gir ikke rett til å kontakte noen. |

## Eierskap og ansvar

| Lag | Eier | Ansvar | Endringsregel |
|---|---|---|---|
| **Bronze** | Kildesystemet | Innhold og korrekthet i den opprinnelige responsen | Lokalt er filer immutable. Cloud raw landing er versjonert, og Databricks har read-only tilgang. |
| **Silver** | Plattformen | Canonical modell, nøkler, typing, deduplisering, validering | Skjemaendringer skal være bakoverkompatible eller varslet til konsumenter. |
| **Gold** | Forretningsdomenet | Definisjon av mål, segmenter og grain | Endret definisjon av et mål er en produktendring, ikke en teknisk detalj. |

I dagens prototype er alle tre rollene den samme personen. Det er nettopp derfor
skillet er skrevet ned: rollene er reelle selv når bemanningen ikke er det.

### Domener

| Domene | Datasett | Persondata |
|---|---|---|
| Sport / kamp | `matches`, `venues`, `weather_observations`, `match_insights` | Nei |
| Supporter / kommersiell | `silver_fans`, `silver_fan_identities`, `silver_ticket_sales`, `fan_activation` | Ja (syntetisk) |

Domenene er bevisst holdt fra hverandre. Et framtidig fan-match-produkt vil
krysse dem, og det er da tilgangsstyring og dataminimering blir en reell
beslutning i stedet for en teoretisk.

Cloud-utvidelsen omfatter bare sport/kamp. De 34 råfilene fra FootballData,
Nominatim og Frost ligger i S3, mens supporterdata med syntetiske
personidentifiserende felter med vilje ikke er flyttet dit.

## Persondata og dataminimering

### Hva som finnes hvor

| Datasett | Personidentifiserende felter | Begrunnelse |
|---|---|---|
| `silver_fans` | Normalisert e-post, visningsnavn | Nødvendig for identitetskobling og kontaktbarhet |
| `silver_fan_identities` | Kildens egne ID-er | Nødvendig for lineage tilbake til kildesystemet |
| `silver_ticket_sales` | `fan_id`, `ticket_customer_id` | Pseudonym kobling; ingen kontaktfelter |
| `fan_activation` | `primary_email`, `display_name` | Nødvendig for direkte målgruppeuttrekk |
| `docs/data/visualizations.json` | Ingen | Kun aggregerte tall for den statiske webappen |

### Anvendte tiltak

- **Kampdomenet er persondatafritt.** `match_insights.parquet` inneholder ingen
  supporterdata, og kan derfor deles bredere enn fan-produktene.
- **Privat og skrivebeskyttet cloud-landing.** S3-bøtten har Block Public
  Access, Bucket Owner Enforced, SSE-S3 og versjonering. Unity Catalog external
  location `clubdata_landing_s3` er read-only og eksponeres gjennom
  `/Volumes/clubdata/bronze/landing_s3`.
- **Lifecycle for rådata i S3.** Gamle ikke-gjeldende versjoner slettes etter
  30 dager, to nyere ikke-gjeldende versjoner beholdes, og ufullstendige
  multipart-opplastinger avbrytes etter 7 dager.
- **Pseudonyme nøkler nedstrøms.** `silver_ticket_sales` bærer `fan_id`, ikke
  kontaktfelter. Atferdsanalyse krever dermed ikke tilgang til e-post.
- **Aggregering før publisering.** Datagrunnlaget til den statiske webappen er et
  aggregert uttrekk uten persondata.
- **Ingen ground truth-identitet i rådata.** Generatorens interne person-ID
  skrives aldri til Bronze.

### Erkjent svakhet

`fan_activation.parquet` inneholder e-post fordi produktet er ment for direkte
målgruppeuttrekk. Det gjør det til det mest sensitive datasettet i prosjektet.
En reell løsning må beskytte det med rollebasert tilgangskontroll, logging av
uttrekk og en retention-policy. S3-sikringen og lifecycle-reglene gjelder bare
det persondatafrie kampdomenet; disse kontrollene finnes ikke for dagens lokale
supporterprototype.

## Samtykke og aktivering

### Modell

Samtykke hentes fra **ticketing**, som er autoritativ kilde. Verdien følger
ticket-identiteten inn i den canonical fanen sammen med `consent_updated_at`.

Samtykke har tre tilstander, ikke to:

| Tilstand | Betydning | Antall i dagens datasett |
|---|---|---|
| `True` | Personen har aktivt samtykket | 283 |
| `False` | Personen har aktivt avslått | 142 |
| Ukjent | Plattformen har ingen ticketing-identitet for fanen | 115 |

App-only fans og uløste appidentiteter får **ukjent** samtykke, ikke et implisitt
avslag. Skillet er viktig: `False` er en beslutning tatt av en person, `Ukjent`
er en mangel hos plattformen. Å slå dem sammen ville skjult et reelt
datakvalitetsproblem og gitt et falskt inntrykk av kontroll.

### Regler for aktivering

`marketing_allowed` i `fan_activation.parquet` er sann bare når **begge** gjelder:

1. `marketing_consent = True` — eksplisitt samtykke.
2. Fanen har en kontaktbar normalisert e-post.

Av 540 fans er 278 aktiveringsbare. Det betyr at segmentering alene ikke gir rett
til å kontakte noen — et segment beskriver atferd, samtykket avgjør handling.

`push_opt_in` fra appen er en **kanalpreferanse** og tolkes aldri som marketing
consent. En person kan ha slått på push uten å ha samtykket til markedsføring.

### Temporal integritet

Silver lagrer bare siste consent-snapshot. Et fan-Gold-bygg avvises derfor hvis
`consent_updated_at` ligger etter valgt `--as-of`, fordi plattformen da ville
brukt en samtykkestatus som ikke fantes på snapshot-tidspunktet.

Det er en bevisst, streng regel: heller feile bygget enn å produsere en
målgruppeliste som ikke kan forsvares i ettertid.

## Syntetiske data

All supporterdata er generert av `src/generate_fan_data.py`.

- Navn og kontaktopplysninger tilhører ikke ekte personer.
- E-post bruker bare de reserverte testdomenene `example.com`, `example.org` og
  `example.net`.
- Kjøpsatferd, samtykke og fragmentering er syntetisk konstruert for å skape
  realistiske identitets- og samtykkeproblemer.
- Genereringen er deterministisk, så datasettet kan gjenskapes eksakt.

Kampdata, stadiondata og værdata er derimot reelle og hentet fra eksterne API-er.
Kombinasjonen gir realistiske tekniske utfordringer uten å behandle
personopplysninger om faktiske supportere.

## Datakvalitet som kontrakt

Validering behandles som en del av governance, ikke bare som teknisk hygiene: et
datasett som stille produserer feil tall er et governance-problem.

- Kritiske brudd **stopper** bygget med en typet exception og tydelig melding.
- Gold valideres mot Silver — samme kamper, samme fans, ingen dupliserte joins.
- Beregnede felter valideres mot sine egne regler, slik at `engagement_segment`
  og `marketing_allowed` ikke kan komme ut av synk med definisjonen.
- Determinisme er testet: uendret input gir byte-identisk output, slik at en
  endring i tall alltid kan spores til en endring i data eller logikk.

Full liste over regler ligger i
[architecture.md](architecture.md#determinisme-og-datakvalitet).

## Kjente gap og plan

Dette er gapene jeg ville tatt tak i først, i denne rekkefølgen.

| # | Gap | Konsekvens i dag | Plan |
|---|---|---|---|
| 1 | Ingen tilgangskontroll for lokale supporterprodukter | Alle med filsystemtilgang kan lese `fan_activation.parquet` med e-post | Rollebasert tilgang per datasett, med fan-produktene som eget nivå |
| 2 | Consent er et snapshot | Kan ikke svare på hva som var lov på et tidligere tidspunkt | Historisert consent som event- eller SCD-modell |
| 3 | Ingen retention-policy for lokale supporterdata | Supporterdata ligger til noen sletter dem manuelt; S3-lifecycle gjelder bare kampdata | Definert levetid per datasett, med automatisk sletting |
| 4 | Ingen sletteflyt for enkeltpersoner | Ingen mekanisme for å etterkomme en sletteforespørsel | Sletting propagert fra kilde gjennom Silver og Gold via `fan_identities` |
| 5 | Ingen formelle data contracts | Konsumenter har ingen skjema- eller SLA-garanti | Versjonert skjema og eierskap per datasett i `docs/` |
| 6 | Ingen lineage-metadata | Opphavet til en verdi må leses ut av koden | Eksplisitt lineage mellom kamp, venue, station og observation |
| 7 | Ingen audit av uttrekk | Ingen sporing av hvem som hentet en målgruppeliste | Logging av uttrekk fra aktiveringsproduktet |
| 8 | Secrets i lokal `.env` | Fungerer lokalt, skalerer ikke til et team | Secrets manager med rotasjon |
| 9 | IaC er bare delvis implementert | S3-bøtten og sikkerhetskonfigurasjonen er Terraform-styrt, men IAM, Databricks storage credential, external location, external volume og Lakeflow-jobben administreres manuelt | Terraform og Databricks Asset Bundles for de gjenværende ressursene |
| 10 | Ingen automatisk CD | GitHub Actions validerer, men deployer eller starter ikke Databricks-jobben | Automatisert deploy og jobbstart med miljøspesifikke godkjenninger |

Prioriteringen følger risiko, ikke teknisk interesse: tilgang til persondata før
skjemagarantier, og sletteflyt før lineage.

GitHub Actions kjører CI med compile-kontroll, 98 unit-tester på Python 3.9 og
3.12, offline `dbt parse` og Terraform-formatkontroll, `init -backend=false` og
`validate` uten AWS-credentials. Automatisk Terraform-plan og apply er ikke
implementert. Dette er ikke full CD. Databricks-jobben leser `main`, men
kjøring og konfigurasjon er foreløpig manuelt styrt.

## Lisenser og attribution

- Geocodingdata kommer fra
  [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) via
  Nominatim og er underlagt ODbL og tjenestens usage policy. Prototypen følger
  policyen med identifiserende User-Agent, sekvensielle kall, maks ett kall per
  sekund og lokal caching.
- Historiske værdata og station metadata kommer fra
  [MET Norway Frost](https://frost.met.no/) og er underlagt MET Norways vilkår og
  datalisensen som oppgis i API-responsene.
- Kampdata er underlagt vilkårene til FootballData-leverandøren.
- API-nøkler ligger i `.env`, som er ignorert av Git. Ingen credentials er
  committet til repoet.

Repoet har foreløpig ingen `LICENSE`-fil. En eksplisitt kode-lisens bør legges
til før prosjektet distribueres eller gjenbrukes offentlig.
