# Plan — MTB Marathon Race Calendar

Last updated: 2025-09-25 (session date 2026-09-25)

## Mission
One spot that shows **all** MTB marathon races (open/citizen races, XCM-style) in **DE, AT, CH, IT, FR**. Not a UCI-elite calendar — those races already have perfect official coverage. We aggregate the long tail.

## Guiding decisions (from initial discussion)
- **Include** open/citizen/gran-fondo style marathon races (roughly 40–120 km MTB events).
- **Exclude** UCI World Championship / elite World Series events (official lists cover them well).
- Countries at launch: Germany, Austria, Switzerland, Italy, **France included from the start**.
- Strategy: combine **official federation lists** + **web scraping** + later **community/social-media discovered races** into one "combined knowledge" dataset.
- Static-first architecture; the dataset is the product, the website is just a view.

## Phase 0 — Scoping & research
- [ ] Finalize race inclusion criteria (distance range? license requirement? UCI XCM label or also pure citizen races?)
- [ ] Inventory data sources per country → `docs/DATA_SOURCES.md`
- [ ] Check legal/ToS aspects of scraping each source (rate limits, attribution)

## Phase 1 — Data model
- [ ] Define JSON schema, one race per record:

```
{
  "id": "slug-unique-per-race-date",
  "name": "Rothaus Bike Marathon",
  "date": "2026-06-14",
  "distances": [{"name": "Marathon", "km": 87, "elevation_m": 1800}],
  "location": "Säckingen",
  "country": "DE",
  "region": "Baden-Württemberg",
  "lat": 47.55, "lng": 7.97,
  "series": "Rothaus Bike Giro",
  "uci_class": null,            // "XCM" | null (citizen race)
  "website_url": "https://…",
  "registration_url": "https://…",
  "sources": ["rad-net", "organizer"],
  "status": "verified",          // verified | unverified | cancelled
  "last_verified": "2026-03-01"
}
```

- [ ] Store dataset in `data/` (JSON, Git-versioned). CSV export optional.

## Phase 2 — Data collection pipeline (the hard part)
- [ ] Scraper framework (Python + requests/BeautifulSoup or Playwright for JS-heavy sites)
- [ ] One scraper per source, each outputs raw records → normalizer merges/dedupes (key: name+date+location fuzzy match)
- [ ] Geocoding via Nominatim (free, attribution required) — cache results
- [ ] Weekly run via GitHub Actions cron; open a PR / commit updated `races.json`
- [ ] Manual "unverified" flag for scraped-but-not-cross-checked races
- [ ] Later: submission flow (GitHub issue template or web form) for races found on social media only

## Phase 3 — Website
- [ ] Static site, no backend; reads `races.json`
- [ ] Tech: Astro or plain HTML+JS (recommend Astro: fast, easy, free hosting)
- [ ] Views:
  - [ ] Filterable/sortable list (country, month, distance, status)
  - [ ] Map view (Leaflet + OpenStreetMap)
  - [ ] Month calendar grid
  - [ ] Per-race detail card: distances, elevation, links to organizer/registration
- [ ] Host on GitHub Pages / Cloudflare Pages / Vercel (free tier)
- [ ] Mobile-first (most traffic will be phones on the trail / in the car)

## Phase 4 — Coverage beyond official lists
- [ ] Submission form for undiscovered races
- [ ] Periodic manual sweep: organizer sites, Instagram/Facebook event pages of known series
- [ ] Reach out to organizers to self-report / claim their race entry

## Phase 5 — Growth (later)
- [ ] SEO: one static page per race ("MTB Marathon Allgäu 2027") → long-tail traffic
- [ ] iCal export + "races near me within 30 days" email
- [ ] More countries (Slovenia, Czechia?), results links, series standings

## Suggested execution order
1. Hand-build dataset for the 2027 season, Germany first (~30–50 races) in JSON
2. Build the site around that JSON
3. Automate: rad-net scraper first (most structured German source)
4. Add AT/CH/IT/FR scrapers
5. Submission flow + community

## Open questions
- Which distance cutoff defines "marathon" for inclusion? (e.g. ≥ 50 km MTB?)
- Include stage races (Transalp etc.) or pure single-day marathons only?
- De-duplication policy when two sources disagree on date/distance — which source wins?
- Hosting/domain choice (e.g. mtbmarathoncalendar.com)?
