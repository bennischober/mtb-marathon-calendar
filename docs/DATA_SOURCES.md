# Data Sources — MTB Marathon Races (DE / AT / CH / IT / FR)

Working document. Verify each source's structure, update terms, and accessibility before writing scrapers.
Status legend: ⬜ unverified / 🟨 promising / 🟩 confirmed useful

## Germany
- ⬜ **rad-net.de** — Germany's most complete race calendar (road/MTB/cyclocross), well-structured HTML, includes MTB marathon events. Likely best first scraper target.
- ⬜ **BDR / Bund Deutscher Radfahrer** — national federation calendar (MTB section)
- ⬜ **Big race series/organizers** (scrape individually later): Rothaus Bike Giro, MTB Bundesliga events, Kroatische Bike Festival-type organizers, Black Forest series
- ⬜ **sportstiming portals** (e.g. sportsvp, raceresult event lists) — race listings often enumerable by organizer

## Austria
- ⬜ **ÖRV / Austrian Cycling (RVS)** — federation event calendar
- ⬜ **radsport-austria.at / austria-cycling.at** calendars
- ⬜ Known series: KTM Mountainbike Marathon Series, Salzkammergut Trophy (also lists other events?), Tour Transalp-type organizers

## Switzerland
- ⬜ **Swiss Cycling** — official event calendar (filter MTB marathon)
- ⬜ **swiss-cycling.fr.ch? / velomarathon.ch** — Swiss marathon series (Rennvelo/MTB)
- ⬜ **ICS/p_Timer-style registration platforms** (datasport.ch, sportstetter) — these registration sites list all their events, scrapeable and reliable
- ⬜ Known events: Grandfondo San Gottardo, Sarnen Bergmarathon-type organizers

## Italy
- ⬜ **Federciclismo** — federation calendar (filter MTB / XCM amateur)
- ⬜ **granfondoguide.com / cicloturismo portals** — amateur race listings
- ⬜ Known: marathon events in South Tyrol (Dolomites), e.g. Val Gardena/Alta Badia area organizers

## France
- ⬜ **FFC (Fédération Française de Cyclisme)** — calendar, section VTT
- ⬜ **La Fabrique / Ufolep** non-license races? Verify
- ⬜ **timing platforms** (chronosport, chrono-montagne?) list events
- ⬜ Known: Roc Marathon (Alpe d'Huez?) — verify current name/date, Lourdes marathon events

## Cross-border / aggregators
- ⬜ **uci.org** — MTB Marathon World Series calendar (use only to *exclude* WC/elite events or to cross-check series dates)
- ⬜ **xcmarathon.com** — German-language MTB marathon portal (verify if maintained)
- ⬜ **Google Maps / Strava club events, Facebook Events** — manual sweep source; hard to scrape reliably, later phase
- ⬜ **Instagram** — organizer accounts announce dates early; later phase, likely manual or via API review

## Scraping notes
- Prefer sources with **structured HTML or underlying JSON APIs** (check network tab in devtools before parsing HTML)
- Respect robots.txt, add delays, cache aggressively, identify scraper UA + contact email
- Every raw record keeps `source` field for provenance & conflict resolution
- Geocoding: OpenStreetMap Nominatim (free, max 1 req/s, requires attribution) — cache lat/lng per unique location string
- Dedupe: normalize name (lowercase, strip accents/umlaut variants), fuzzy match (e.g. rapidfuzz) + exact date match → merge, prefer federation/organizer source over aggregator

## TODO for next session
- [ ] Verify rad-net.de structure & build first scraper
- [ ] Check each federation site: is there a JSON/XHR endpoint behind the calendar?
- [ ] Test Nominatim geocoding caching setup
