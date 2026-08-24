# Governance: ansvarlig bruk av data

Governance handler om hvem som bestemmer over dataene, hvem som får bruke dem,
og hvilke regler som må følges. I Klubbdata betyr det særlig å skille mellom
analyse av aktivitet og retten til å kontakte en supporter.

> [!IMPORTANT]
> Dette er en demonstrasjon med syntetiske supporterdata. Dokumentet viser
> prinsipper og tekniske tiltak, men er ikke en juridisk vurdering eller en
> erstatning for en reell personvernvurdering.

## Hvorfor dette er en del av løsningen

En teknisk riktig kobling er ikke nødvendigvis riktig å bruke. En dataplattform
må også kunne svare på:

- Hvem eier definisjonen av et tall?
- Hvor kommer opplysningen fra?
- Inneholder datasettet persondata?
- Hvem har lov til å se eller bruke det?
- Hadde personen gyldig samtykke på det aktuelle tidspunktet?

Disse spørsmålene behandles som en del av designet, ikke som kontroll i etterkant.

## Eierskap og ansvar

| Rolle | Ansvar i en reell løsning | Eksempel i prosjektet |
|---|---|---|
| **Kildesystemet** | Eier den opprinnelige registreringen | Billettsystemet eier samtykkestatusen det har samlet inn. |
| **Dataplattformen** | Rydder, kobler og kontrollerer data | Silver lager felles ID-er og stopper ugyldige koblinger. |
| **Forretningsområdet** | Eier betydningen og bruken av ferdige mål | Kommersiell side må definere hva «aktiv supporter» betyr. |
| **Personvern og sikkerhet** | Setter krav til tilgang, lagring og sletting | Supporterdata må beskyttes strengere enn kampdata. |

I prototypen utføres alle rollene av samme person. Skillet er likevel viktig,
fordi en produksjonsløsning må fordele beslutninger og ansvar tydelig.

Plattformen skriver aldri tilbake til kildesystemene. Den lager en felles
arbeidsmodell, men overtar ikke eierskapet til den opprinnelige opplysningen.

## Persondata og dataminimering

Dataminimering betyr å bruke så få personopplysninger som formålet tillater.

| Datatype | Persondata | Hvordan den brukes |
|---|---|---|
| Kamp, stadion og vær | Nei | Analyse og offentlig webvisning |
| Supporteridentiteter | Ja, men syntetisk | Kobling mellom billettsystem og app |
| Billetttransaksjoner | Pseudonym supporter-ID | Aktivitetsanalyse uten kontaktfelt |
| `fan_activation.parquet` | Navn og e-post, men syntetisk | Demonstrasjon av et direkte målgruppegrunnlag |
| `fan_segment_summary.parquet` | Nei | Aggregerte tall per aktivitetsgruppe |
| Publisert porteføljedata | Nei | Statisk webvisning |
| Maskinlæringsgrunnlag | Nei | Isolert segmenteringseksperiment |

`fan_activation.parquet` er det mest sensitive datasettet fordi det kombinerer
kontaktinformasjon, aktivitet og samtykke. En reell løsning måtte hatt
rollebasert tilgang, logging av uttrekk og automatisk lagringstid. Disse
kontrollene finnes ikke i den lokale prototypen.

### Tiltak i prototypen

- Kampdelen er helt uten persondata og kan deles bredere.
- Kontaktfelt holdes utenfor billettanalysen; transaksjoner bruker en intern
  `fan_id`.
- Den offentlige eksporten leser bare aggregerte og persondatafrie resultater.
- Eksporten stopper hvis et forbudt felt eller en e-postadresse dukker opp.
- Supporterdata er ikke flyttet til S3 eller den ordinære Databricks-jobben.
- Maskinlæringen bruker aktivitetstall, ikke navn, e-post eller samtykke.

## Samtykke og aktivering

Billettsystemet er autoritativ kilde for markedsføringssamtykke. Plattformen
kopierer og kontrollerer statusen, men finner ikke på et samtykke selv.

Samtykke har tre tilstander:

| Tilstand | Betydning | Dagens demonstrasjonsdata |
|---|---|---:|
| **Ja** | Personen har aktivt samtykket | 283 |
| **Nei** | Personen har aktivt avslått | 142 |
| **Ukjent** | Plattformen mangler en koblet samtykkekilde | 115 |

«Ukjent» er ikke det samme som «nei». Nei er et valg personen har tatt, mens
ukjent viser at plattformen mangler informasjon. Ingen av dem gir rett til å
kontakte personen.

En supporter kan bare markeres som `marketing_allowed` når begge krav er
oppfylt:

1. Samtykket er eksplisitt **ja**.
2. Supporteren har en kontaktbar, normalisert e-postadresse.

I demonstrasjonsdataene oppfyller 278 av 540 supportere begge kravene.

Aktivitetsgruppe eller maskinlæringssegment endrer aldri denne regelen.
`push_opt_in` fra appen er en kanalinnstilling, ikke et markedsføringssamtykke.

### Riktig status på riktig tidspunkt

Supporterproduktet bygges med en eksplisitt `--as-of`-dato. Bygget stopper hvis
en samtykkestatus ble oppdatert etter denne datoen. Det hindrer løsningen i å
bruke informasjon som ikke var kjent på tidspunktet målgruppen skulle gjelde
for.

Prototypen lagrer bare siste samtykkestatus. En produksjonsløsning må historisere
endringer for å kunne dokumentere hva som var gyldig tidligere.

## Syntetiske data

Alle supporter- og billettdata lages av `src/generate_fan_data.py`:

- navn og kontaktopplysninger tilhører ikke ekte personer
- e-postadresser bruker reserverte testdomener
- kjøp, samtykke og identitetsproblemer er konstruert for demonstrasjonen
- samme kjøring lager det samme datasettet

Kamp-, stadion- og værdata er ekte. Kombinasjonen gir realistiske tekniske
utfordringer uten å behandle opplysninger om faktiske supportere.

## Datakvalitet som kontrakt

Datakvalitet er også governance: et datasett som stille gir feil svar kan ikke
forvaltes ansvarlig.

Derfor stopper dataflyten blant annet når:

- påkrevde ID-er eller tidspunkt mangler
- samme kamp eller supporter blir duplisert av en kobling
- beregnede segmenter ikke følger den definerte regelen
- samtykke og kontaktstatus ikke gir riktig `marketing_allowed`
- publiseringsfilen inneholder personidentifiserende felt

Uendret input gir samme output i samme miljø. Dermed kan endringer i tall spores
til endringer i data eller logikk. Se [arkitekturen](architecture.md#determinisme-og-datakvalitet)
for den tekniske gjennomføringen.

## Kjente gap og plan

Prototypen mangler blant annet produksjonsklar tilgangsstyring,
samtykkehistorikk, automatisk sletting, revisjonslogg og full automatisering av
skyløsningen. Gapene og anbefalt rekkefølge er samlet i
[begrensninger og vei videre](limitations.md).

## Kilder og bruksvilkår

- Kampdata kommer fra [FootballData](https://footballdata.io/).
- Stadionplasseringer kommer fra
  [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) via
  Nominatim.
- Historiske værdata kommer fra [MET Norway Frost](https://frost.met.no/).
- API-nøkler ligger i en lokal `.env`-fil som er ignorert av Git.

Koden følger [MIT-lisensen](../LICENSE). Eksterne data følger vilkårene og
lisensene til de respektive kildene.