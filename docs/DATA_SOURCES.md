# Data Sources — MTB Marathon Races (DE / AT / CH / IT / FR)

Working inventory for the automated ingestion layer. Verify each source's current structure, terms, robots policy, and update cadence before enabling an adapter.

Status legend: ⬜ unverified / 🟨 promising / 🟩 confirmed useful

## Source priority

1. Official race/series pages and official calendars
2. National and regional cycling federation calendars
3. Public event platforms and registration/timing systems
4. Specialist calendars and aggregators
5. Automated web discovery through public search APIs, sitemaps, and public feeds

The source priority is a default conflict-resolution rule, not a reason to discard lower-priority sources. Every source record remains traceable.

## Germany

- 🟨 **radsport-events.de** — its MTB Marathon calendar appears to cover many German and international series and is a strong candidate for the first high-coverage adapter. Verify HTML structure, query parameters, date semantics, and terms.
- 🟨 **German Cycling / Bund Deutscher Radfahrer (BDR)** — official German cycling federation calendars and MTB-related series. The BDR Radmarathon-Cup is a useful source of long-distance events, but it is largely road/Gran-Fondo oriented; only include records with clear MTB evidence.
- ⬜ **BDR regional associations** — regional calendars can reveal races absent from the national list. Treat them as separate sources with their own provenance.
- ⬜ **rad-net.de** — broad German race calendar; useful as a supporting source, but likely requires careful MTB filtering.
- ⬜ **Direct organizer/series pages** — Rothaus Bike Giro, MTB Bundesliga, Black Forest and regional marathon series. Discover these automatically from calendars and series links, then maintain adapters only for high-value or high-volume organizers.

## Austria

- ⬜ **Austrian Cycling / national federation calendar** — verify the current official name, domain, and MTB calendar structure.
- 🟨 **Salzkammergut Marathon organizer sources** — important because one-day ultra-distance events may exceed 200 km and 7,000 m elevation; the classifier must not discard them based on a 50 km threshold.
- ⬜ **Major Austrian MTB series and organizers** — including KTM/Mountainbike Marathon-style series and ultra/epic events.
- ⬜ **Public event platforms and timing providers** — often expose a complete list of their events in one place.

## Switzerland

- ⬜ **Swiss Cycling calendar** — official federation source; filter for MTB marathon, long-distance, and epic formats.
- 🟨 **Swiss Epic and other stage/epic organizers** — multi-day races must be represented with `date_start`, `date_end`, and `format: stage_race`.
- ⬜ **Swiss event platforms and registration providers** — useful for enumerating many events and retrieving registration links.
- ⬜ **Regional and local organizer pages** — important for the long tail that national calendars omit.

## Italy

- ⬜ **Federciclismo calendar** — filter MTB, marathon, and amateur/enduro categories carefully.
- ⬜ **Regional federations and local organizer pages** — South Tyrol, the Dolomites, and other mountain regions require local discovery.
- ⬜ **Public event platforms and timing providers** — useful for enumerating event series and dates.
- ⬜ **Specialist Italian MTB calendars** — verify maintenance and coverage before relying on them.

## France

- ⬜ **Fédération Française de Cyclisme (FFC)** — official calendar, especially the VTT section.
- ⬜ **Regional French cycling calendars** — likely necessary because national coverage will miss many citizen races.
- ⬜ **French organizer pages and public event platforms** — include both one-day marathons and multi-day/ultra formats.
- ⬜ **Non-license and federation-adjacent listings** — verify carefully to avoid importing road/gravel events.

## Cross-border and automated discovery

- ⬜ **UCI calendar** — use mainly to identify and exclude World Championship/elite World Series events, not as the primary discovery source.
- ⬜ **Public search APIs** — use an approved search API rather than scraping search-engine result pages directly. Search combinations should include `MTB marathon`, `mountainbike marathon`, `marathon VTT`, `ultra-enduro`, `MTB epic`, `stage race MTB`, and local-language equivalents.
- ⬜ **Sitemaps and RSS/ICS feeds** — follow organizer and series sitemaps/feeds to discover event pages that are not linked from a central calendar.
- ⬜ **Social media** — use public APIs/feeds only where permitted. Do not bypass login walls or anti-bot protections; provide submission links for unreachable discoveries.
- ⬜ **Specialist aggregators** — useful for cross-checking, but never the sole source for a race record.

## Fields to extract automatically

- Event name, stable event/series identity, and edition/year
- Start and end dates
- One-day, point-to-point, stage, or multi-day format
- Course variants: name, distance, elevation, start/finish, technical notes, and availability
- Location, venue, country, region, coordinates, and area/terrain information
- Series/organizer and relevant discipline/category
- Official event page and registration page
- Previous-edition links, results links, attendance, and historical route information when publicly available
- Source URL, retrieval timestamp, extraction confidence, supported fields, and cancellation/status

## Future enrichment and review sources

- **Official previous-edition pages and results** — use for historical course details, attendance, route changes, and links to prior years; keep these attached to the relevant edition.
- **Organizer route/course pages and public GPS/maps** — extract course descriptions, elevation profiles, terrain, and scenic/area information where permitted.
- **Tourism, park, and regional landscape sources** — useful for objective location and terrain descriptions, not subjective race reviews.
- **Community reports and public reviews** — store separately with author/contributor, date, source URL, and clear subjective labeling; do not blend them into automated facts.
- **Human review queue** — capture accepted/rejected classifications and false-positive reasons without deleting the original candidate or source evidence.

## Automation and scraping policy

- Use public pages and documented feeds/APIs only. Check robots.txt, terms, rate limits, and licensing before enabling a source.
- Prefer RSS, ICS, JSON, XML, sitemaps, JSON-LD, and ordinary HTML tables over browser automation.
- Use conditional requests, caching, delays, and a descriptive User-Agent with a contact address.
- Never bypass CAPTCHAs, login walls, access controls, or technical anti-bot measures.
- Keep raw responses or hashes and enough source metadata to reproduce and audit every generated record.
- Run optional LLM extraction only on public, unstructured text after structured extraction fails; validate its output and retain confidence scores.
- Geocode with OpenStreetMap Nominatim or an equivalent service, cache results, respect usage limits, and display required attribution.
- Track `last_success`, item count, parse-error rate, extraction coverage, and reviewed false-positive rate per source; stale or failing sources must be visible.

## Initial adapter backlog

- [ ] `radsport-events.de` MTB Marathon calendar
- [ ] BDR/German Cycling and relevant regional calendars
- [ ] Swiss Cycling and Swiss Epic organizer/series pages
- [ ] Austrian federation and Salzkammergut Marathon sources
- [ ] Federciclismo and Italian regional calendars
- [ ] FFC and French regional calendars
- [ ] Public event/timing platforms with broad coverage
- [ ] Automated sitemap/RSS/ICS discovery and source-health monitoring
