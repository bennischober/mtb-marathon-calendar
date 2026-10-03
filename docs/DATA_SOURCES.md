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

## Verified sources

### 🟩 radsport-events.de

Verified on 2026-10-02 by inspecting the live site and API.

**Access**

- The website is a client-side rendered React application. There is no server-rendered HTML to parse, and the canonical event page `https://radsport-events.de/events/{id}` is only useful for outbound links.
- The application reads a public JSON API, which is both parsable and more structured than the page: `https://api.radsport-events.de/api/v1/events`.
- `robots.txt` on `radsport-events.de` allows public paths and excludes account and admin areas. The API host returns 403 for `robots.txt`, which RFC 9309 treats as allow.
- Terms of use and any reuse or rate-limit conditions for the API have **not** been confirmed. Treat the adapter as provisional until that is settled, and keep the request delay conservative.

**Query and pagination**

- Request `category=MTB` and `status=PUBLISHED` to narrow the result set.
- The API caps page size at 100 records and silently returns fewer rows when a larger size is requested, so the adapter asks for 100 and stops paging on `totalPages`.
- `content` holds the records, each with `title`, `eventType`, `category`, `eventDate`, optional `endDate`, `country`, `description`, coordinates, `organizer`, `organizerUrl`, and a `distances` array carrying per-distance `distanceKm` and `elevationMeters`.
- A single run returned 218 MTB-category records. Only upcoming events are exposed; there is no date-range filter, so archived results cannot be re-read.

**Why it is worth keeping but not trusting**

- This is a road, gravel and cyclocross aggregator first. Its MTB category is heavily polluted with cyclocross, CTF, hillclimb, downhill, duathlon, standard enduro, gravel, and road events, so most records must be discarded by the shared classifier.
- Conversely it is a good source for exactly the hard cases the calendar cares about: one-day ultras well over 200 km such as the Salzkammergut Trophy, and multi-day stage races such as the Tour de Mur and the Trans Balkan Race. A distance threshold alone would wrongly drop them.
- Discipline labels are coarse and occasionally wrong, and some records are technique courses or club rides filed as MTB. The classifier therefore also uses a title check for training-style listings and always records why it decided what it decided.
- Some organizer URLs arrive without a scheme (`example.de/race`) and are normalised before validation.
- Because labels are unreliable, treat this as a discovery source whose records need review, not as an authoritative calendar.

## Germany

- 🟩 **radsport-events.de** — implemented as the first adapter. The public site is a client-side rendered React app with no server-rendered HTML, so the adapter reads the public JSON API the same app uses: `https://api.radsport-events.de/api/v1/events`. See "Verified sources" below.
- ⛔ **Bund Deutscher Radfahrer (BDR) / German Cycling** — *discarded as a machine source.* Verified 2026-10-02:
  - `https://www.bdr.de/` **302-redirects to `bundesdruckerei.de`**, an unrelated printing company. The old domain is dead and must not be used.
  - `https://bdr-online.org/` 200-redirects to `https://www.rad-net.de/`, so the federation's web presence is rad-net and nothing else.
  - There is no public Radmarathon-Cup calendar or feed at any BDR-controlled URL. The federation is effectively only reachable through rad-net (below).
- ⛔ **rad-net.de** — *do not automate.* Its `robots.txt` carries an explicit, 60-entry denylist of AI and search crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended, and similar), each with `Disallow: /`. Our user agent is not named there, but the intent is unambiguous. Separately, the public calendar shell returns no event data without a member session, so the "Termine und Wettkampfausschreibungen" pages are login-walled. Treat rad-net as a **manual reference only**.
- 🟨 **Regional federation calendars (PDF)** — *implemented as `bawue-radsportverband`.* The BAD/WTB Baden-Württemberg calendar (`badischer-radsportverband.de`) serves a public season PDF with a permissive `robots.txt` (`User-agent: *`, only `/typo3/` and `/typo3_src/` disallowed). Its tables extract reliably with `pypdf` and carry a discipline column, so MTB races are separable from road, track and gravel. Verified against the 2026 season: 114 rows, of which 37 are MTB and 8 are marathons.
  - **What the source cannot provide**, and therefore never invents: distances (every record carries `no_distance_data`), per-event web pages (`official_url` points at the calendar PDF), and venue coordinates (only a coarse association `Region` such as `Ost` or `Schwaben-Zollern`). Each gap is reported as an extraction warning and can be filled in later through `data/curated/`.
  - **Maintenance:** the PDF is re-uploaded each season under a new `Stand_<date>` filename and is not linked from any HTML page or sitemap, so `SEASON_PDF_URL` must be updated annually.
  - **Parser notes:** rows are single extracted text lines, so columns are matched by their code vocabulary rather than by offset; a specific discipline code always beats the bare `MTB` marker (a row listed `MTB XCO` is cross-country and stays out of scope even when its title says "Mountainbike-Marathon"). Cancelled races are marked by `ABGESAGT` where the optional end date would be, and a typo such as a 2026 race printed as `21.05.2065` is corrected to the season year and reported as `date_year_corrected_from_2065_to_2026`.
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

- 🟨 **Fédération Française de Cyclisme (FFC)** — *verified 2026-10-03, adapter recommended.* The competition calendar at `competitions.ffc.fr/calendrier/calendrier.aspx` is public, server-rendered and filterable: ASP.NET postback filters (discipline, group, department, level) work from plain HTTP when `__VIEWSTATE`, `__VIEWSTATEGENERATOR` and `__EVENTVALIDATION` are carried through. Event rows are dated (`Le dd/mm/yyyy`), link to per-event pages carrying organizer and duration, and the default view spans 2026 (502 events) and 2027 (237, not yet VTT-populated).
  - **Reality check on yield**: "VTT - Cross country" (filter 9) is licensed XC racing; the marathon-adjacent mass sits in the *Randonnée* group (9 VTT upcoming incl. Roc d'Azur XCM) and in *Cyclosportive* (road sportives only, 20). Expect ~30-40 relevant French events per year, not hundreds. The dozens of independent French marathon organizers remain reachable only through organizer-direct discovery.
  - Sample extraction verified end to end: discipline, date, title, department, organizer, duration all parse cleanly.
- ⬜ **Regional French cycling calendars** — likely necessary because national coverage will miss many citizen races.
- ⬜ **French organizer pages and public event platforms** — include both one-day marathons and multi-day/ultra formats.
- ⬜ **Non-license and federation-adjacent listings** — verify carefully to avoid importing road/gravel events.

## Cross-border and automated discovery

- 🟩 **stageraces.com** — *implemented as the discovery source (`discover` command).* Verified 2026-10-03:
  - `robots.txt` allows everything (`User-agent: *` with empty Disallow) and publishes an event sitemap (228 stage races worldwide).
  - Event pages are server-rendered WordPress with the race name in `<h1>` and a description in `og:description`; most lack dates and coordinates, so this source **proposes** races — the calendar entry must come from the organizer's own page.
  - The discovery pipeline crawls the sitemap, pre-filters on five-country signals, screens survivors with the LLM (one call per candidate, full description — batched evaluation measurably degraded verdict quality), and reports in-scope races we do not list yet. Proposals are never auto-imported.
  - Verified results from one run: **Alpentour Trophy (AT)**, **Appenninica (IT)**, **Raid VTT Les Chemins du Soleil (FR)**, **Transmaurienne Vanoise (FR)**, **Rally di Romagna (IT)** proposed as new; Swiss Epic correctly recognized as already covered.
  - Note: the site rate-limited after ~250 requests/hour during testing; the crawler's parallelism and delays are set conservatively.
- ⛔ **utagawa.com** — *blocked.* `robots.txt` wildcard section: `Disallow: /`.
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

- [x] `radsport-events.de` MTB Marathon calendar — adapter implemented against the public JSON API
- [x] BDR/German Cycling **investigated and rejected** (dead domain, rad-net agent denylist)
- [x] Regional federation calendar PDF **implemented** as `bawue-radsportverband` (BAD/WTB, Baden-Württemberg)
- [ ] Swiss Cycling and Swiss Epic organizer/series pages
- [ ] Austrian federation and Salzkammergut Marathon sources
- [ ] Federciclismo and Italian regional calendars
- [ ] FFC and French regional calendars
- [ ] Public event/timing platforms with broad coverage
- [ ] Automated sitemap/RSS/ICS discovery and source-health monitoring
