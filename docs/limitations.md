# Begrensninger og vei videre

Klubbdata viser hvordan en liten dataplattform kan bygges fra kilde til ferdig
innsikt. Prosjektet viser metode og tekniske valg, men er ikke en ferdig løsning
for drift i en klubb.

Dette dokumentet samler de viktigste grensene for hva demoen kan brukes til, og
hva som måtte vært på plass før reelle supporterdata eller operative prosesser
kunne tas inn.

## Hva resultatene ikke beviser

- **Supporter- og billettdata er syntetiske.** Tallene beskriver et konstruert
  demonstrasjonsdatasett, ikke faktiske supportere eller salg i FK Bodø/Glimt.
- **Solgte billetter er ikke oppmøte.** Prosjektet mangler data fra innslipp og
  kan derfor ikke si hvem som faktisk var på kamp.
- **Værdekningen er begrenset.** Kamper uten en egnet målestasjon står uten vær
  i stedet for å få et usikkert estimat.
- **Maskinlæring har ikke dokumentert forretningsverdi.** Forsøket viser en
  sporbar metode, men forbedringen er for liten og datagrunnlaget for kunstig
  til å forsvare bruk i praksis.
- **Én klubb brukes som eksempel.** Lag-ID, enkelte regler og dagens
  datakilder er valgt for denne demoen, ikke som en generell klubbplattform.

## Tekniske og organisatoriske gap

| Område | Begrensning i dag | Hva en reell løsning trenger |
|---|---|---|
| Tilgang til supporterdata | Lokale Parquet-filer har ikke rollebasert tilgang eller logging av uttrekk | Tilgang per rolle og datasett, revisjonslogg og godkjent eksportflyt |
| Lagring og sletting | Lokale supporterdata har ingen automatisk levetid eller sletteflyt for enkeltpersoner | Regler for lagringstid og sletting som følger personen gjennom alle datalag |
| Samtykke | Bare siste kjente status lagres | Historikk som viser hva som var gyldig på et bestemt tidspunkt |
| Identitetskobling | Kilder kobles bare ved en unik, normalisert e-postadresse | Faglig godkjente regler, manuell behandling av tvilstilfeller og historikk |
| Databehandling | Silver og Gold bygges på nytt i sin helhet | Inkrementell behandling når datamengde og kjørekostnad gjør det nødvendig |
| Geokoding | Første søkeresultat brukes automatisk | Kvalitetsscore eller manuell kontroll av usikre stadiontreff |
| Skyinfrastruktur | S3 og en utviklingsjobb er kodebasert, men resten av Databricks-oppsettet er bare delvis automatisert | All infrastruktur definert som kode, egne miljøer og sikker håndtering av tilstand og tilgangsnøkler |
| Levering | GitHub Actions tester løsningen, men utrulling og jobbstart er manuell | Godkjent og automatisert utrulling med overvåking og tilbakeføring |
| Datakontrakter | Regler finnes i kode og tester, men ikke som formelle avtaler | Versjonerte skjemaer, eierskap, kvalitetskrav og forventet leveringstid |
| Maskinlæring | Ingen modellregistrering, servering, driftsovervåking eller automatisk retrening | Faglig validering, personvernvurdering, overvåking og menneskelig godkjenning |

Skyløsningen dekker foreløpig bare kampdata. Supporterdata er med vilje
holdt utenfor S3 og Databricks, bortsett fra et minimert og syntetisk
featuregrunnlag uten navn, e-post eller samtykke.

## Prioritert vei mot produksjon

1. **Beskytt persondata først.** Innfør rollebasert tilgang, sikker lagring av nøkler,
   logging av uttrekk, lagringstid og en verifiserbar sletteflyt.
2. **Gjør historikken etterprøvbar.** Historiser samtykke og identiteter, og
   etabler versjonerte datakontrakter med tydelig eierskap.
3. **Bygg en trygg driftsmodell.** Fullfør Infrastructure as Code, skill mellom
  utvikling og produksjon, og automatiser utrulling med godkjenning og overvåking.
4. **Utvid datagrunnlaget før analysene.** Legg til faktiske oppmøtedata og
   relevante app-hendelser før prediktive modeller eller kamprettet aktivering.
5. **Vurder maskinlæring på nytt til slutt.** Reelle data, dokumentert formål,
   personvernvurdering, faglig effekt og menneskelig kontroll må være på plass.

Rekkefølgen følger risiko og verdi: kontroll på persondata kommer før mer
avansert analyse og mer automatisering.

## Bevisste avgrensninger

Noe er utelatt fordi det ville gjort demonstrasjonen større uten å gjøre
hovedpoenget tydeligere:

- full produksjonsplattform og døgnkontinuerlig drift
- automatisk utrulling til Databricks
- generell støtte for flere klubber og valgfrie datakilder
- probabilistisk identitetsmatching uten en verifisert fasit
- prediktiv aktiveringsscore uten oppmøte- og hendelsesdata
- produksjons-MLOps før modellen har dokumentert nytte

Se [arkitekturen](architecture.md) for hvordan løsningen er bygget,
[governance](governance.md) for ansvar, persondata og samtykke, og
[ML- og AI-strategien](ml-ai-strategy.md) for modellbeslutningen.