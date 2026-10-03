# Plan — MTB Marathon Race Calendar

Last updated: 2026-10-03

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

## Core now vs enrichment later

### Core automated model
The first useful release should capture:
- Event identity and edition/year
- Start/end dates and format
- Country, region, location, and coordinates
- Overall distance/elevation summary
- Course variants when available
- Official and registration links
- Source provenance, freshness, classification confidence, and status

### Future enrichment
The same records can later gain:
- Separate small/medium/large course variants with distances, elevation, start/finish, and technical details
- Previous editions, historical course information, attendance/results, and year-over-year changes
- Area/location profiles and map/route links
- Human-written race profiles covering route character, scenery, organization, and planning experience
- Human review decisions, including accepted/rejected classifications and explicit false-positive flags

Automated facts, human decisions, and subjective editorial assessments should be stored separately. A previous year's information should link to the previous edition rather than being copied into the current edition as if it were current.

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
- [x] Build a source registry: URL, country, coverage, update cadence, format (JSON/HTML/PDF/RSS/ICS), parser status, terms/robots status, and last successful run. Registry facts are code (`SourceProfile`) so they are reviewed in pull requests; observed state lives in the append-only `collection_runs` table and is reported by `mtb-pipeline status`.
- [ ] Check technical and legal feasibility per source; prefer official feeds, sitemaps, calendars, and structured endpoints over brittle HTML scraping.
- [x] Define source priority: official organizer/series page > official federation/association calendar > public event platform > specialist aggregator > general web discovery. Encoded as `PRIORITY_RANK` on each source profile.
- [ ] Define confidence and conflict rules for automated records.

## Phase 1 — Data model and automation contracts
- [x] Model a stable event/series identity separately from each year-specific edition.
- [x] Model course variants as child records; do not flatten small/medium/large distances into one ambiguous event.
- [x] Keep source evidence at record or field level so automated facts can be audited and corrected.
- [x] Keep automated facts, human review decisions, and subjective editorial content separate.
- [x] Store generated records and curated overlays separately (`data/generated/` and `data/curated/`), then merge them at publish time so scheduled ingestion cannot erase human decisions. Overlays may correct fields, attach editorial profiles and record reviews, but cannot invent events, change ids, or edit provenance. See `docs/CURATED_DATA.md`.
- [x] Define JSON schema, one edition per record:

```
{
  "id": "example-mtb-epic-2027",
  "event_id": "example-mtb-epic",
  "edition": 2027,
  "name": "Example MTB Epic",
  "date_start": "2027-06-14",
  "date_end": "2027-06-18",
  "format": "stage_race",
  "duration_days": 5,
  "location": {
    "name": "Sankt Wolfgang",
    "country": "AT",
    "region": "Salzburg",
    "lat": 47.9,
    "lng": 13.4
  },
  "course_variants": [
    {
      "id": "example-mtb-epic-2027-long",
      "name": "Long",
      "distance_km": 240,
      "elevation_m": 7000,
      "start_location": "Sankt Wolfgang",
      "status": "announced",
      "source_refs": ["source-123"]
    }
  ],
  "previous_edition_id": "example-mtb-epic-2026",
  "official_url": "https://…",
  "registration_url": "https://…",
  "source_refs": [
    {
      "id": "source-123",
      "url": "https://…",
      "retrieved_at": "2026-03-01T12:00:00Z",
      "supports": ["name", "date_start", "course_variants"],
      "confidence": 0.9
    }
  ],
  "classification": "mtb_stage_race",
  "classification_confidence": 0.95,
  "review": {
    "status": "unreviewed",
    "reason": null,
    "reviewed_by": null,
    "reviewed_at": null
  },
  "status": "announced",
  "last_verified": "2026-03-01"
}
```

- [x] Add optional `editorial_profile` data for route character, scenery, organization, technical difficulty, and planning/review notes. It must be labeled as editorial or community-derived content rather than an automated fact.
- [ ] Add optional historical fields such as `results_url`, `attendance`, `previous_edition_id`, and a route/format change summary. Derive previous editions by `event_id` where possible; never copy last year's course data into the current edition without evidence.
- [ ] Use review statuses `unreviewed`, `approved`, `rejected`, and `false_positive`. `approved` means accepted into our calendar after human review, not official race approval. A false positive must retain the original candidate, reason, reviewer, and timestamp for classifier evaluation.
- [ ] Store edition-level review decisions separately from an event-level recurrence assessment; a prior approval can prioritize next year's discovery but cannot confirm a new edition automatically.
- [ ] Store generated data in `data/` as JSON; keep raw source snapshots or hashes for auditability.
- [ ] Add optional `source_text_hash`, `registration_opens`, and `last_changed_at` fields.

## Repository architecture

This will remain a **monorepo** rather than being split into separate repositories. The public site and data pipeline have separate runtime boundaries, but share versioning, schema definitions, fixtures, and release automation.

- `apps/site` — Astro public website; static, read-only, and independent of the database
- `services/pipeline` — Python CLI/worker for source discovery, fetching, extraction, analysis, normalization, review flags, and exports
- `packages/schema` — versioned JSON Schema and generated language bindings/types used as the contract between pipeline and site
- `data/generated` — validated machine-generated JSON consumed by the site
- `data/curated` — human-maintained review and editorial overlays
- `db` — schema migrations, repository queries, and local database tooling
- `raw` — local source snapshots, kept out of Git
- `infra` — local containers, deployment configuration, and operational tooling

Dependency direction is `services/pipeline → packages/schema → data/generated → apps/site`; the site must not import pipeline internals or query the database.

## Implementation details — persistence and frontend

### Persistence
- Use **PostgreSQL as the production source of truth** for events, editions, course variants, sources, ingestion runs, reviews, and editorial profiles.
- Use **SQLite for local development and the first prototype** behind the same repository/service layer; migrate to PostgreSQL before multiple writers, a review UI, or regular production ingestion.
- Keep the public website independent of the database: ingestion writes validated records to PostgreSQL, then generates a read-only JSON artifact for the static site.
- Treat `data/generated/` as machine-generated output and `data/curated/` as human-maintained overlays; merge them during publishing so scheduled jobs cannot overwrite reviews or editorial work.
- Use migrations, transactions, idempotent upserts, and immutable source observations keyed by source URL/content hash. Preserve change history for dates, courses, and status.
- Store raw HTML, PDF, ICS, and GPS files in a local `raw/` directory during development or object storage in production; keep only metadata, hashes, and paths in the database. Do not commit large raw files or database files to Git.

Current status: the first persistence slice is implemented with SQLAlchemy 2 against SQLite (`services/pipeline/src/mtb_pipeline/orm.py`, `database.py`, `repository.py`), exposed through `mtb-pipeline init-db`, `ingest`, and `export`. Idempotent upserts and exact JSON round-tripping are covered by tests. `source_observations`, `reviews`, and `ingestion_runs` tables plus Alembic migrations are still to come, and the ORM models are expected to move under `db/` once migrations exist.

Suggested core entities:

- `events` — stable race/series identity and recurrence metadata
- `editions` — year/date-specific calendar records
- `course_variants` — small/medium/large or stage-specific routes
- `sources` — source definitions, terms status, and health metadata
- `source_observations` — extracted facts, evidence, confidence, and retrieval time
- `reviews` — approval/rejection/false-positive decisions
- `editorial_profiles` — clearly labeled human/community information
- `ingestion_runs` — scheduled-run status, errors, counts, and coverage

### Pipeline stack
- Use Python for the pipeline because the workload is dominated by HTTP retrieval, HTML/PDF/feed parsing, normalization, geocoding, and optional structured LLM extraction.
- Manage the Python project with `uv`: the repo root is a uv workspace (`services/pipeline` is a member), `.python-version` pins the interpreter, and `uv.lock` pins resolved dependencies. Use `uv sync` and `uv run` so no `pip install` or manual virtualenv step is needed.
- Use `httpx` for HTTP, `selectolax` or BeautifulSoup for HTML, `feedparser` for RSS, `icalendar` for ICS, `pypdf` for PDFs, and Playwright only for permitted JavaScript-heavy pages.
- Use Pydantic for configuration and validated extraction models; use RapidFuzz for entity/name matching.
- Use SQLAlchemy 2 with Alembic for SQLite/PostgreSQL portability and migrations; use Typer for the CLI.
- Add pytest and Ruff to the pipeline quality gates. A type checker is still undecided.
- Keep geocoding, LLM extraction, and external search behind provider interfaces so they can be replaced, cached, or disabled independently.
- Expose explicit commands: `collect`, `validate`, `review`, and `export`. `export` is the only command that writes the site-facing artifact.

### Frontend
- Use **Astro + TypeScript** for the public static site. The calendar is primarily read-only, so React is not needed by default.
- Keep the first version mostly static HTML/CSS with small vanilla TypeScript modules for filtering, sorting, and calendar interactions; use Preact islands only where a richer widget is justified.
- Use Leaflet for the map, loading map data from the generated JSON artifact at build time.
- Add a separate review/admin interface later. That interface may use React or another component framework because it has different state and interaction requirements; it should write through a small API or repository layer rather than modifying generated files directly.
- If the public site later needs application-like state, introduce React/Svelte islands incrementally rather than converting the entire site to a client-rendered framework.

## Phase 2 — Automated ingestion pipeline
- [x] Polite fetch layer: robots.txt handling, retry on transient failures, per-host delay, descriptive User-Agent, content hashing, and content-addressed raw snapshots with retrieval metadata.
- [x] Source adapter framework with a `fetch`/`parse` split, so parsing can be replayed against recorded bytes offline.
- [x] First live adapter: `radsport-events` against the public JSON API (see `docs/DATA_SOURCES.md`).
- [x] Second live adapter, and the first `official_federation` source: `bawue-radsportverband` parses the BAD/WTB Baden-Württemberg season PDF. The German national federation was investigated and rejected (`bdr.de` is a dead domain and `rad-net.de` disallows AI crawlers), so a regional association was the only robots-clean, machine-readable German source.
- [x] Deterministic scope filter on source-stated labels (facts only), then LLM-primary classification: every surviving record is a `mtb_candidate` until the LLM — reading title, description and distances via an OpenAI-compatible API — assigns the final class (`mtb_marathon`, `mtb_ultra`, `mtb_stage_race`, `mtb_point_to_point`, `exclude`). Answers are cached in `llm_cache` by content, model and prompt version; without `MTB_LLM_API_KEY` the run degrades to all-candidates, never silently dropping records.
- [x] Scope filtering by country and classification, with a review candidate tier instead of silent deletion.
- [x] `collect` CLI with `--dry-run`, `--delay`, `--limit`, `--country`, and `--raw-dir`.
- [x] Source adapters for JSON and PDF (`radsport-events`, `bawue-radsportverband`).
- [x] **Generic LLM extraction replaces per-site parsing** (verified 2026-10-03): one generic prompt ("extract event facts, dates may be in any language, today is ...") produced correct validated records from French, German and Italian organizer pages with zero site-specific code. The per-site *parsing* layer (regexes, column maps) is obsolete for new sources; an "adapter" shrinks to: fetch (robots/limits/snapshots stay!) → LLM-extract → Pydantic-validate. Known LLM failure modes to keep in the prompt: too-conservative date extraction (fixed by "use null only when the text truly lacks the fact") and wrong-year conversion (fixed by giving today's date + the upcoming-edition rule). What stays deterministic: fetching/politeness, discovery of *where* to look (sitemaps, filter navigation), validation, provenance.
- [ ] Source adapters for XML/RSS, ICS, HTML, and sitemaps; use Playwright only where permitted and necessary.
- [x] Normalize dates, country/location, event format, URLs, and edition identity.
- [x] Extract course variants separately, including distance, elevation, and technical notes. Start/finish locations and per-variant availability still need sources that expose them.
- [ ] Geocode locations with a cached geocoder (OpenStreetMap Nominatim or equivalent), respecting usage limits and attribution requirements.
- [ ] Match likely recurring editions across years so current events can inherit historical links without inheriting stale facts.
- [x] Deduplicate with normalized names plus date/location signals; retain all source references. Matching is token-based and conservative: a distinctive name token plus a compatible date and country are required, and generic words like "Bike Marathon" never match on their own.
- [x] Resolve conflicts field-by-field using source priority and freshness; never silently discard a disagreement. The winner is chosen by source priority then retrieval time, and every field where sources disagreed is reported by `export` with both values retained.
- [x] Store merge conflicts durably. Each conflict records the edition it affects, the field, the value that won, the source it came from, the discarded value and why it lost. `mtb-pipeline conflicts` lists them, so a surprising merge can be checked instead of trusted. The table reflects the latest export rather than accumulating history.
- [x] Validate required fields and obvious false positives, recording why each decision was made.
- [x] Route low-confidence classifications and suspected false positives to a human-review queue while keeping the automated result intact.
- [x] LLM connection via config file (`mtb-pipeline.toml`, gitignored; `mtb-pipeline.example.toml` documents the format) with precedence CLI flags > `MTB_LLM_*` env vars > config file; discovered from the working directory, `services/pipeline/` or the repo root. Provider deviations are tolerated: bare JSON answers and unknown classification labels degrade to `mtb_candidate` instead of crashing.
- [x] Stale-edition pruning: a collect run deletes its own adapter's editions it no longer produces, so classification changes (e.g. the LLM now excluding a race) cannot leave phantom rows in the database.
- [x] Known-events registry (`data/curated/known-events.json`): races we expect to exist are checked against every export, and known-but-missing races are reported so a silent aggregator gap becomes visible.
- [ ] ~~Run scheduled jobs through GitHub Actions~~ — dropped (2026-10-03): the workflows will not be used. Collection runs locally via the CLI when wanted; the artifact is rebuilt and committed by hand. The workflow file remains in the repo but is dead configuration, not a plan item.
- [x] Track per-source freshness, failure rate, and extraction coverage so the system knows when its calendar is stale. `mtb-pipeline status` reports `ok`/`stale`/`failing`/`never_run` per source against its declared cadence.

## Phase 3 — Website
- [x] Static site, no public backend or direct database access; reads the generated dataset.
- [x] Tech: Astro + TypeScript, with small vanilla TypeScript modules for interactivity.
- [x] Views:
  - [x] Filterable list in the modern faceted-search pattern: one quiet toolbar (search, Filters button, sort), the filter panel opens on demand, and active filters show as removable lozenges with a count badge. Filters are opt-in restrictions (nothing checked = show all). Groups: When (upcoming/past/all-time), Class (marathon/ultra/stage/point-to-point/needs-review), Format, Country, Month, and a dual-thumb distance slider (capped at 200 km+; ultras beyond it match at the cap). Races from before the current date are hidden by default. The review-status filter was removed from the public site: review state is internal workflow information, not visitor-facing.
  - [ ] Map view (Leaflet + OpenStreetMap) — deferred. Adding a JS map library conflicts with the dependency-light approach, and the federation sources publish region labels rather than coordinates, so the view would be mostly empty. Event pages link to OpenStreetMap when a source does give coordinates.
  - [x] Single-column race cards with a left scan rail (country flag, compact date, year): identity on the left edge, title + format/days badges right-aligned on a fixed scan line, place/distance/meta below, distance pills only for multi-variant races.
  - [x] Month calendar as a one-month pager: icon-arrow prev/next flanking the month select (which doubles as the title), a Today button, sticky toolbar under the app header, keyboard ←/→, hash deep links, multi-day races spanning all their days (continuations marked), weekend/today highlighting, and a mobile day-card list fallback.
  - [x] Event detail page in the same design language: hero card with flag/date rail, breadcrumbs, action buttons (official/registration/map, all opening in new tabs with rel=noopener), course-variant cards instead of a table, provenance rows, and sibling-edition links with flags. Duplicate distance labels are collapsed (a variant named "45 km" renders once).
  - [x] Previous-edition history, where the collected data contains more than one edition of an event.
  - [ ] Later: course-variant selector, and clearly labeled editorial/community information.
- [x] Make source and freshness visible on each event; every detail page lists its source URLs, retrieval times, confidence, and which fields each source supplied, and unreviewed records are badged rather than presented as confirmed.
- [ ] ~~Host on GitHub Pages / Cloudflare Pages / Vercel~~ — deployment dropped along with the scheduled workflows; the site builds locally and the artifact is committed. Revisit only if the site is ever published.
- [x] Mobile-first (most traffic will be phones on the trail or in the car).

## Phase 4 — Automated discovery beyond known calendars
- [ ] Use public search APIs, sitemaps, RSS/ICS feeds, series pages, and event-platform listings to discover obscure races.
- [ ] Add organizer/series discovery: follow links from known calendars and extract linked event pages automatically.
- [ ] Use public social APIs or permitted feeds where available; otherwise provide a low-effort submission/claim form for discoveries that automation cannot reach.
- [ ] Periodically re-check event pages for date changes, cancellations, and newly announced editions.

### Human review and enrichment
- [ ] Maintain a review queue for ambiguous classifications, conflicting sources, recurring editions, and suspected false positives.
- [ ] Let reviewers approve/reject candidates and record a reason, timestamp, and reviewer identity; preserve the original automated extraction.
- [ ] Use accepted prior editions to prioritize discovery of the next year's recurrence, without automatically treating it as confirmed.
- [ ] Add structured course and location information first; add qualitative race profiles afterward with clear editorial/community attribution.
- [ ] Collect route/scenery/organization/experience information only from permitted public sources or explicit human contributions, and label subjective statements.

## Phase 4.5 — The editorial layer (direction shift, 2026-10-03)

The race list alone is not the differentiator: mature listing platforms
(radsport-events.de et al.) already cover the raw "where/when" for Germany,
and we consume them as sources. The defensible layer above the data is
**trip-planning context** — most of which is more automatable than assumed.
Ordered by cost, all reusing the existing pipeline, provenance model and
review states:

- [x] **"Same week, nearby"** on every detail page — other races in the same
  region within ±N days, computed purely from our own calendar. Zero
  editorial effort; turns the site from a list into a trip planner.
- [ ] **"Make a week of it"** — area context per race: other races in the
  region within a wider window, notable trail networks and sights from
  public tourism sources, summarized by the LLM with source attribution and
  stored as clearly derived content.
- [ ] **Route character from facts** — derived, labeled descriptions from
  the data we already have: elevation profile, distance spread, stage count
  ("gravel grind vs alpine epic"). No new collection needed.
- [ ] **Community report excerpts** — LLM-summarized public race reports
  with author/source attribution, stored separately from automated facts
  (per the existing enrichment rules). Never blended into facts.
- [ ] **Honest firsthand atmosphere** — the one genuinely non-automatable
  piece; only from own experience or user submissions. Fine to never do.

The guiding rule stays: derived content is labeled as derived, editorial
content as editorial, and nothing ever loses its source links.

## Phase 5 — Growth (later)
- [ ] SEO: one static page per event ("MTB Epic Allgäu 2027") → long-tail traffic.
- [ ] iCal export and "races near me within 30 days" notifications.
- [ ] More countries, results links, and series standings.

## Suggested execution order
1. Define the event classification rules, stable event/edition identity, course-variant model, and source registry.
2. Build the ingestion framework and test it against a small set of fixtures.
3. Automate high-value sources first: radsport-events.de MTB Marathon listings, regional German federation calendars, and major event platforms/organizer series. The national BDR/German Cycling calendar turned out not to exist as an automatable source; see `docs/DATA_SOURCES.md`.
4. Add federation and country-specific adapters for AT, CH, IT, and FR.
5. Add automated discovery from sitemaps/search APIs, course/history extraction, and deploy the static site.
6. Add the human-review queue, false-positive feedback loop, and editorial enrichment only after the automated core is reliable.

## Automation-first success criteria
- The majority of newly published events originate from automated adapters, not hand entry.
- Every event has at least one traceable source URL and retrieval timestamp.
- Source freshness and automated coverage are measurable per country and source.
- Course-variant coverage and recurring-edition matching are measurable where source data permits.
- False positives from XCO/DH/road/gravel feeds are identified and excluded automatically or sent to review.
- Date changes and cancellations propagate without requiring a full manual rebuild.

## Open questions
- Should ultra-distance events without an explicit "marathon" label be included automatically or held as candidates?
- Should stage races be shown as one event, as daily stages, or both?
- ~~Which source wins when two sources disagree?~~ Decided: source priority first (organizer, then federation, then platform, then aggregator), retrieval freshness as the tie-breaker, and every disagreement is recorded rather than dropped. See `merge_editions` in `services/pipeline/src/mtb_pipeline/dedupe.py`.
- What licensing/attribution requirements apply to each source and to extracted data, historical results, and editorial/community content?
- How should subjective information such as scenery, route quality, and overall organization be rated and attributed?
- Which review roles and approval workflow should be used before an item is marked `approved`?
