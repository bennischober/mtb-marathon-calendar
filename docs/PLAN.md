# Plan — MTB Marathon Race Calendar

Last updated: 2026-09-25

## Mission
An automated-first calendar that aggregates as much publicly available information as possible about MTB marathon races in **DE, AT, CH, IT, FR**. It is a discovery and comparison layer, not a replacement for organizers' official pages or registration systems.

The product is a continuously refreshed, provenance-aware dataset. The website is one view over that dataset.

## Guiding decisions
- **Automation first:** source discovery, fetching, extraction, classification, normalization, deduplication, validation, and publishing should be automated wherever technically and legally possible.
- **Secondary role for curation:** manual review and submissions are fallbacks for ambiguous, missing, or blocked sources—not the primary way to populate the calendar.
- **Include** open/citizen MTB marathons, ultra-distance MTB events, point-to-point events, and multi-day stage/epic races such as Swiss Epic.
- **Exclude** XCO, XCC, downhill, standard competitive enduro, road/time-trial, and gravel-only events. Include ultra-enduro or marathon-style enduro when the event is explicitly a long-distance or one-day ultra. UCI World Championship/elite World Series events are excluded from the default view because their official coverage is already strong.
- A distance of roughly **50 km or more is an indicator**, not a hard inclusion rule. The Salzkammergut Marathon (~200 km and ~7,000 m elevation) and similar one-day events must qualify despite being much longer than a typical marathon.
- Countries at launch: Germany, Austria, Switzerland, Italy, and France.
- Every published record must retain its source URL, retrieval time, and confidence/verification state.

## Event classification model

The ingestion pipeline should classify candidates using page content, event categories, and metadata—not just the event title.

- `mtb_marathon`: explicitly MTB and marathon/ultra/long-distance, including marathon-style ultra-enduro
- `mtb_stage_race`: multi-day stage or epic format
- `mtb_point_to_point`: point-to-point format, if marathon-distance
- `mtb_candidate`: plausible event requiring classification/confirmation
- `exclude`: XCO, XCC, DH, standard competitive enduro, road/TT, gravel-only, or non-marathon event

Keep `distance_km_min`, `distance_km_max`, `elevation_m`, `duration_days`, `format`, and `classification_confidence` as separate fields. Missing distance data must not automatically exclude an event when the organizer explicitly labels it a mountainbike marathon or epic.

"XCM" is the UCI discipline label for cross-country marathon racing. It is not a universal label for citizen races, so the pipeline must recognize common organizer terms such as *MTB marathon*, *mountainbike marathon*, *marathon MTB*, *ultra-enduro*, *marathon*, and *epic/stage race* in context. A multi-day epic is still a mountainbike marathon event, but its format should be represented separately from a single-day marathon.

## Phase 0 — Scoping and source feasibility
- [ ] Define the first release as "as complete as possible", not a promise of literal completeness.
- [ ] Build a source registry: URL, country, coverage, update cadence, format (JSON/HTML/PDF/RSS/ICS), parser status, terms/robots status, and last successful run.
- [ ] Check technical and legal feasibility per source; prefer official feeds, sitemaps, calendars, and structured endpoints over brittle HTML scraping.
- [ ] Define source priority: official organizer/series page > official federation/association calendar > public event platform > specialist aggregator > general web discovery.
- [ ] Define confidence and conflict rules for automated records.

## Phase 1 — Data model and automation contracts
- [ ] Define JSON schema, one event per record:

```
{
  "id": "slug-unique-per-event-and-edition",
  "name": "Example MTB Epic",
  "date_start": "2027-06-14",
  "date_end": "2027-06-18",
  "distance_km_min": 180,
  "distance_km_max": 240,
  "elevation_m": 7000,
  "format": "stage_race",
  "location": "Sankt Wolfgang",
  "country": "AT",
  "region": "Salzburg",
  "lat": 47.9,
  "lng": 13.4,
  "series": "Example Series",
  "classification": "mtb_stage_race",
  "official_url": "https://…",
  "registration_url": "https://…",
  "sources": [
    {
      "name": "source-id",
      "url": "https://…",
      "retrieved_at": "2026-03-01T12:00:00Z",
      "confidence": 0.9
    }
  ],
  "status": "active",
  "last_verified": "2026-03-01"
}
```

- [ ] Store generated data in `data/` as JSON; keep raw source snapshots or hashes for auditability.
- [ ] Add optional `source_text_hash`, `registration_opens`, and `last_changed_at` fields.
- [ ] Treat `official_url` and `registration_url` as outbound links; the site must never attempt to register users or replace those pages.

## Phase 2 — Automated ingestion pipeline
- [ ] Source adapters for JSON, XML/RSS, ICS, PDF, HTML, and sitemaps; use Playwright only where permitted and necessary.
- [ ] Extract structured data first (JSON-LD, embedded state, tables, feeds), then use targeted text extraction and optional LLM-assisted extraction for unstructured pages.
- [ ] Normalize dates, country/location, distances, elevation, event format, and URLs.
- [ ] Geocode locations with a cached geocoder (OpenStreetMap Nominatim or equivalent), respecting usage limits and attribution requirements.
- [ ] Deduplicate with normalized names plus date/location signals; retain all source references.
- [ ] Resolve conflicts field-by-field using source priority and freshness; never silently discard a disagreement.
- [ ] Validate required fields, future/past dates, reachable links, and obvious false positives.
- [ ] Run scheduled jobs (initially daily for high-value sources, weekly for the long tail); publish changes through GitHub Actions and deploy only validated output.
- [ ] Track per-source freshness, failure rate, and extraction coverage so the system knows when its calendar is stale.

## Phase 3 — Website
- [ ] Static site, no backend; reads the generated dataset.
- [ ] Tech: Astro or plain HTML+JS (recommend Astro: fast, easy, free hosting).
- [ ] Views:
  - [ ] Filterable/sortable list (country, month, distance, format, status)
  - [ ] Map view (Leaflet + OpenStreetMap)
  - [ ] Month calendar grid
  - [ ] Event detail card with distances, elevation, format, and outbound organizer/registration links
- [ ] Make source and freshness visible on each event; do not imply that an automatically discovered event is officially confirmed unless it is.
- [ ] Host on GitHub Pages / Cloudflare Pages / Vercel (free tier).
- [ ] Mobile-first (most traffic will be phones on the trail or in the car).

## Phase 4 — Automated discovery beyond known calendars
- [ ] Use public search APIs, sitemaps, RSS/ICS feeds, series pages, and event-platform listings to discover obscure races.
- [ ] Add organizer/series discovery: follow links from known calendars and extract linked event pages automatically.
- [ ] Use public social APIs or permitted feeds where available; otherwise provide a low-effort submission/claim form for discoveries that automation cannot reach.
- [ ] Periodically re-check event pages for date changes, cancellations, and newly announced editions.

## Phase 5 — Growth (later)
- [ ] SEO: one static page per event ("MTB Epic Allgäu 2027") → long-tail traffic.
- [ ] iCal export and "races near me within 30 days" notifications.
- [ ] More countries, results links, and series standings.

## Suggested execution order
1. Define the event classification rules and source registry.
2. Build the ingestion framework and test it against a small set of fixtures.
3. Automate high-value sources first: radsport-events.de MTB Marathon listings, BDR/German Cycling calendars, and major event platforms/organizer series.
4. Add federation and country-specific adapters for AT, CH, IT, and FR.
5. Add automated discovery from sitemaps/search APIs and deploy the static site.
6. Add submissions and exception review only for gaps automation cannot resolve.

## Automation-first success criteria
- The majority of newly published events originate from automated adapters, not hand entry.
- Every event has at least one traceable source URL and retrieval timestamp.
- Source freshness and automated coverage are measurable per country and source.
- False positives from XCO/DH/road/gravel feeds are identified and excluded automatically.
- Date changes and cancellations propagate without requiring a full manual rebuild.

## Open questions
- Should ultra-distance events without an explicit "marathon" label be included automatically or held as candidates?
- Should stage races be shown as one event or as daily stages?
- Which source wins when two sources disagree on date or distance: newest page, organizer page, or highest-confidence extraction?
- What licensing/attribution requirements apply to each source and to extracted data?
- Which country/source should be added first after the initial German pipeline?
