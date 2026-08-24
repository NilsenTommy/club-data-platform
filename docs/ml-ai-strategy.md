# ML- og AI-strategi

Dette dokumentet beskriver det eksplorative segmenteringseksperimentet for
supporterdomenet, hva resultatene viser, og hvilke krav som må være oppfylt før
en modell kan vurderes for produksjon.

> [!IMPORTANT]
> Dette er en teknisk demonstrasjon på syntetiske data. Modellen er ikke
> produksjonsklar og brukes ikke til automatiserte supporterbeslutninger.

## Forretningsspørsmål

Eksperimentet undersøker om aktivitets- og kjøpsmønstre kan gi meningsfulle
supportersegmenter som supplement til dagens deterministiske regler. Det finnes
ingen fasitlabel for hvilken gruppe en supporter tilhører. Oppgaven er derfor
**unsupervised learning**: K-means leter etter struktur i featuredataene uten å
trenes mot `rule_segment` eller et annet mål.

Alle 540 supportere og alle supporterdata i eksperimentet er syntetiske. Det gjør
datasettet egnet til å demonstrere metode, dataminimering og sporbarhet, men det
kan ikke dokumentere forretningsverdi, representativitet eller effekt på reelle
supportere.

## Datagrunnlag og dataminimering

Modellen trenes bare på syv PII-frie aktivitetsfeatures:

1. `recency_days`
2. `matches_purchased_12m`
3. `purchase_transactions_12m`
4. `tickets_purchased_12m`
5. `total_spend_12m`
6. `cancelled_transactions_12m`
7. `refunded_transactions_12m`

`fan_id`, `as_of_at`, `window_start_at`, `rule_segment` og
`marketing_allowed` brukes ikke i trening. Navn, e-post, kontaktdata og
samtykkefelter finnes heller ikke i featuregrunnlaget. `fan_id` brukes bare til
å koble det ferdige segmentnavnet tilbake til riktig syntetisk supporter,
mens `rule_segment` brukes som en etterfølgende sammenligningsreferanse.
Segmenteringen påvirker aldri `marketing_allowed`.

## Eksperimentdesign

Pipelinen bruker `log1p`, standardisering og K-means med `random_state=42` og
`n_init=20`. Kandidater fra `k=2` til `k=6` ble sammenlignet med silhouette
score. Høyeste score vinner, med laveste `k` som tie-break ved identisk score.
Stabilitet ble målt som Adjusted Rand Index (ARI) mot baseline over fem faste
alternative seeds.

| k | Silhouette | Inertia | Minste segment | Minste andel | Stability ARI, snitt/min |
|---:|---:|---:|---:|---:|---:|
| 2 | 0.6388378 | 1523.1907 | 117 | 21.67 % | 1.0 / 1.0 |
| 3 | 0.5822753 | 1011.8039 | 68 | 12.59 % | 1.0 / 1.0 |
| **4** | **0.6391666** | **570.8235** | **67** | **12.41 %** | **1.0 / 1.0** |
| 5 | 0.5647993 | 410.5804 | 67 | 12.41 % | 1.0 / 1.0 |
| 6 | 0.5638179 | 351.1528 | 37 | 6.85 % | 1.0 / 1.0 |

`k=4` ble valgt med silhouette `0.6391666`. Forskjellen fra `k=2`, som fikk
`0.6388378`, er bare omtrent `0.00033`. Fire segmenter er derfor ikke entydig
bedre enn to; valget følger den definerte målingen, men må behandles som en
hypotese for faglig vurdering. Stability ARI var `1.0` for alle kandidater på
tvers av de fem seedene. Det viser at løsningene var stabile på dette faste,
syntetiske datasettet, ikke at de vil være stabile på reelle eller endrede data.

## Valgt segmentering

K-means-labelene ble canonicalisert deterministisk fra lavere til høyere
aktivitet. Den valgte modellen ga følgende segmenter:

| Segment | Antall | Tolkning |
|---|---:|---|
| `ML_01` | 115 | **Inaktivt segment.** Ingen registrerte kjøp eller aktivitet i analysevinduet. |
| `ML_02` | 77 | **Refusjonspreget segment.** Aktiv kjøpsatferd, men tydelig høyere forekomst av refusjoner. |
| `ML_03` | 67 | **Kanselleringspreget segment.** Aktiv kjøpsatferd, men tydelig høyere forekomst av kanselleringer. |
| `ML_04` | 281 | **Bredt aktivt segment.** Den største aktive gruppen, uten tilsvarende refusjons- eller kanselleringspreg. |

ARI mellom ML-segmentene og de regelbaserte segmentene var omtrent `0.3133`.
ARI er **ikke accuracy**: målet beskriver likheten mellom to partisjoneringer og
er invariant mot segmentnavn. `rule_segment` er heller ikke ground truth. Den
moderate likheten viser at K-means fanger noe av den samme aktivitetsstrukturen,
men også deler supporterne etter kanselleringer og refusjoner som regelverket
ikke uttrykker på samme måte.

## Modellbeslutning

Eksperimentet er **teknisk vellykket som eksplorativ demonstrasjon**. Dataene
valideres, treningsgrunnlaget er minimert, modellvalget er deterministisk,
stabiliteten er målt og eksperimentet spores med hosted MLflow.

Beslutningen er likevel å **ikke produksjonssette modellen**. Den marginale
forskjellen mellom `k=4` og `k=2`, syntetiske data og manglende dokumentasjon av
forretningseffekt gir ikke grunnlag for operativ bruk. Det er ikke opprettet
modellregistrering, serving endpoint eller automatiserte supporterbeslutninger.

## Krav før produksjon

Før en segmenteringsmodell kan vurderes for reell bruk, må minst følgende være
på plass:

- **Reelle og representative data:** Modellen må utvikles og valideres på data
  som dekker faktiske supportermønstre, sesongvariasjon og relevante kanaler.
- **Behandlingsgrunnlag og DPIA:** Formål, databruk, lagring og tilgang må ha et
  dokumentert behandlingsgrunnlag. Behovet for og utfallet av en DPIA må
  avklares før persondata brukes.
- **Faglig validering:** Kommersielt ansvarlige og supporterfaglige eiere må
  vurdere om segmentene er forståelige, stabile og handlingsrelevante.
- **Definerte tiltak:** Hvert segment må ha et legitimt, dokumentert tiltak og
  tydelige kriterier for når modellen ikke skal brukes.
- **Fairness-vurdering:** Segmentene og tiltakene må undersøkes for skjevheter,
  indirekte diskriminering og uønsket ekskludering.
- **Overvåking:** Datakvalitet, featurefordelinger, segmentstørrelser, stabilitet
  og faktisk effekt må overvåkes over tid.
- **Drift:** Eierskap, tilgangskontroll, lineage, hendelseshåndtering,
  rollback og kostnadsansvar må være definert.
- **Retreningskriterier:** Det må finnes eksplisitte terskler for drift,
  databrudd og ytelsesendring som utløser ny vurdering eller retrening.
- **Menneskelig godkjenning:** Segmenter skal støtte analyse, ikke alene utløse
  kontakt eller andre tiltak. Operativ bruk krever menneskelig beslutning og
  separat kontroll av gyldig samtykke.
