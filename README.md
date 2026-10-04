# MTB Marathon Race Calendar

An automated-first calendar that aggregates publicly available information about mountain bike marathon races in **Germany, Austria, Switzerland, Italy, and France** — including one-day ultra marathons and multi-day stage/epic races.

> **Scope decision:** This is a discovery and comparison layer, not a replacement for official race pages or registration systems. We exclude UCI World Championship / World Series premium events from the default view and focus on open/citizen races that are scattered across organizer sites, federation lists, event platforms, and the wider web.

## The one-paragraph system overview

The **pipeline** (Python CLI, `services/pipeline`) collects race data from public sources into SQLite, classifies every record with an LLM, deduplicates overlapping sources with per-field conflict resolution, enriches races from their organizers' own pages (descriptions, prices, courses), and exports a validated JSON artifact. The **site** (Astro, `apps/site`) is a static read-only view over that artifact — list, calendar, finder, planner, archive, and per-event pages. Human corrections live in `data/curated/` and are merged on top at export time; **human data is never overwritten by aggregation** (see the [data-treatment contract](docs/PLAN.md#data-treatment-contract-fundamental)).

```
sources ──collect──▶ SQLite ──export──▶ events.json ──build──▶ static site
   ▲                     │                  │
   │                     │ enrich           │ merge overlays (humans win)
 discovery               ▼                  ▼
 (proposals)        organizer pages     data/curated/*.json
```

## Goal

A continuously refreshed, provenance-aware dataset built primarily through automated ingestion:
1. **Official feeds and calendars** (organizers, federations, series)
2. **Public event platforms, sitemaps, and web scraping**
3. **Automated web discovery** through approved search APIs and public feeds
4. **Submissions and exception review** only as a fallback for gaps automation cannot reach

The website links out to the original event and registration pages; it does not handle registration itself.

## The exact workflow (copy-paste-able)

Everything runs through `uv` (Python) and `npm` (site). From the repo root:

> **Path caveat:** the CLI resolves relative paths against its working directory.
> `uv run --directory services/pipeline mtb-pipeline …` runs *inside*
> `services/pipeline/`, so `data/mtb.sqlite3` and `tests/fixtures/events.json`
> resolve there; when running from the repo root (`uv run mtb-pipeline …`, which
> works because the root venv exposes the CLI), `data/mtb.sqlite3` is the root one.
> Pick one style and stay with it — the examples below use `--directory`.

### 0. One-time setup

```sh
uv sync                                   # Python deps + .venv at the repo root
cd apps/site && npm install && cd ../..   # site deps
cp services/pipeline/mtb-pipeline.example.toml services/pipeline/mtb-pipeline.toml
#   then edit mtb-pipeline.toml: set base_url / model / api_key for your LLM
```

The LLM config file is **gitignored** (it carries a secret). Without it the pipeline still works — records stay `mtb_candidate` and nothing is dropped, but classification and enrichment/discovery are unavailable.

### 1. Initialize the database (first time, or after ORM model changes)

```sh
uv run mtb-pipeline init-db                    # default: data/mtb.sqlite3
uv run mtb-pipeline init-db --reset            # drop + recreate (required after model changes)
uv run mtb-pipeline init-db --database path/to/db.sqlite3
```

### 2. Collect from live sources

```sh
uv run mtb-pipeline collect --source radsport-events            # biggest aggregator (JSON API)
uv run mtb-pipeline collect --source bawue-radsportverband      # BAD/WTB federation season PDF
uv run mtb-pipeline collect --source epic-series                # IRONMAN Epic Series (Swiss Epic …)
uv run mtb-pipeline collect --source ffc                        # French federation calendar (LLM-extracted)
```

Options (all optional except `--source`):

| Flag | Meaning | Default |
| --- | --- | --- |
| `--source <name>` | Source adapter to run; one of the four above | required |
| `--database <path>` | SQLite file | `data/mtb.sqlite3` |
| `--country <CC>` | Scope filter, repeatable (`--country DE --country AT`) | DE, AT, CH, IT, FR |
| `--limit <n>` | Stop after roughly n records | page everything |
| `--delay <seconds>` | Minimum delay between requests to one host | 1.0 |
| `--dry-run` | Full fetch/parse/classify, write nothing | off |
| `--raw-dir <path>` | Where raw page snapshots go (audit trail) | `raw/` |
| `--user-agent <ua>` | UA string; set a real contact address before unattended runs | placeholder |
| `--llm-model <model>` | Classification model (overrides config) | from config |
| `--llm-base-url <url>` | OpenAI-compatible endpoint (overrides config) | from config |
| `--llm-cache <path>` | SQLite file caching LLM verdicts | `data/llm-cache.sqlite3` |
| `--config <path>` | Explicit config TOML (missing file = error) | auto-discovered |

What it does: fetches the source's pages (robots.txt-checked, delayed, snapshotted under `raw/<source>/` with content hashes), extracts facts, then the **LLM classification stage** assigns every record its final class (`mtb_marathon`, `mtb_ultra`, `mtb_stage_race`, `mtb_point_to_point`, or `exclude`). Verdicts are cached by (content, model, prompt version): re-collecting unchanged data costs **zero** API calls. Survivors are written to SQLite; editions this run no longer produces are pruned (no stale rows). The run itself is recorded in `collection_runs` — failures included.

**Degraded mode:** with no LLM configured, collection still works; every record stays `mtb_candidate` (visible on the site as needing review) and the run summary says `DEGRADED`.

### 3. Enrich from organizer pages (descriptions, prices, courses)

```sh
uv run mtb-pipeline enrich                            # all races
uv run mtb-pipeline enrich --max-races 5              # first 5 only (trials)
uv run mtb-pipeline enrich --database path/to/db.sqlite3
```

Reads each race's official page, follows its own links to subpages (`ausschreibung`, `strecken`, `gebuehren`…), and merges what it finds — **additively and hash-checked**: a page whose content hash is unchanged since the last read is skipped (zero LLM cost); a changed page is re-extracted and its facts replace the stored ones (a removed course propagates). Fields the organizer publishes outrank aggregator values; existing facts are never replaced on a timer. Federation-PDF races (no organizer site) are skipped and keep their visible gaps. Needs the LLM.

### 4. Discover new races (proposals, never auto-imports)

```sh
uv run mtb-pipeline discover                  # crawl the discovery source, screen with the LLM
uv run mtb-pipeline discover --max-candidates 40
uv run mtb-pipeline discover --json           # machine-readable report
```

Crawls `stageraces.com`'s event sitemap (permissive robots), pre-filters candidates on five-country signals, then screens each survivor with the LLM (one call per candidate, full description — deliberately not batched, which measurably degrades verdict quality). In-scope races we do not list yet are **proposed** with name, URL, country and reason; a human decides what enters the calendar. Needs the LLM.

### 5. Ingest a JSON file directly (imports, fixtures)

```sh
uv run mtb-pipeline ingest --input path/to/events.json
uv run mtb-pipeline ingest --input path/to/events.json --database other.sqlite3
```

Validates and upserts editions from a bare JSON array or a full export envelope. Idempotent: re-running updates existing editions, course variants and sources in place — **and preserves human review/editorial data** (see the contract below).

### 6. Export the site artifact

```sh
uv run mtb-pipeline export                                        # database → site artifact
uv run mtb-pipeline export --database data/mtb.sqlite3            # explicit db
uv run mtb-pipeline export --no-curated                           # machine data only
uv run mtb-pipeline export --input fixture.json                   # fixture-based export (no db)
uv run mtb-pipeline export --generated-at "2026-10-03T12:00:00Z"  # reproducible exports
uv run mtb-pipeline export --output /tmp/other.json               # different destination
```

Three fixed stages: dump → dedupe/conflict-resolution → curated overlays → (re-)validate. Writes `data/generated/events.json`, the full-calendar `data/generated/events.ics`, and one `.ics` per edition under `data/generated/events/` (used by the site's Calendar buttons). Stores every field disagreement in the `merge_conflicts` table.

### 7. Inspect

```sh
uv run mtb-pipeline status                       # per-source health: ok/stale/failing/never_run
uv run mtb-pipeline status --json
uv run mtb-pipeline conflicts                    # what disagreed in the last export
uv run mtb-pipeline conflicts --json
uv run mtb-pipeline conflicts --edition <id>     # one edition only
uv run mtb-pipeline validate --input tests/fixtures/events.json   # schema-check a JSON file
```

### 8. Build and serve the site

```sh
cd apps/site
npm run build        # dist/
npm run dev          # dev server with hot reload
npm run preview      # serve the built dist/ (what production would show)
```

## LLM configuration

`services/pipeline/mtb-pipeline.toml` (copy from `mtb-pipeline.example.toml`; gitignored):

```toml
[llm]
base_url = "https://api.openai.com/v1"   # any OpenAI-compatible endpoint
model = "gpt-4o-mini"                    # cheap mini-class models are enough
api_key = "sk-..."
```

Precedence: **CLI flags > `MTB_LLM_API_KEY` / `MTB_LLM_BASE_URL` / `MTB_LLM_MODEL` environment variables > config file.** The config is discovered from the working directory, `services/pipeline/`, or the repo root — independent of where you invoke the CLI. The key is never logged, never exported, never stored in the database.

## The data-treatment contract (fundamental)

How data is treated when processes run again — the system's core invariant:

1. **Human data wins forever.** Review decisions and editorial profiles survive every re-collect. Aggregation proposes; humans decide. (Enforced in the repository upsert layer.)
2. **Facts are per-source, per-time.** Every fact carries where and when it was read (`source_refs` with url, timestamp, content hash). Re-runs update facts only when the source page changed.
3. **Unchanged content is never re-judged.** The classification LLM is only asked about content with no cached verdict; identical content re-collected = zero API calls. Enrichment skips unchanged pages the same way.
4. **Descriptions follow the organizer, not the clock.** Replaced only when the organizer's page actually changed; never on a schedule that could override a human correction.
5. **Series vs edition ownership.** Facts about one event date (dates, start time, courses, prices, status) live on the edition. Human editorial (route character, scenery, "worth a vacation") lives on the series and rides on the latest edition at publish time — displayed as inherited, never copied.

Full rationale: `docs/PLAN.md`, "Data-treatment contract".

## Curated corrections (how humans edit)

Overlays live in `data/curated/*.json` (committed to Git, applied last on every export). One file, any number of entries; each addresses one **edition** (`edition_id`) or a whole **series** (`event_id` — rides on the series' latest edition):

```json
{
  "event_id": "radsport-events-bike-festival-riva-del-garda-it",
  "editorial": {
    "summary": "Big festival atmosphere at Lake Garda.",
    "route_character": "Punchy climbs around the lake, fast descents.",
    "contributor": "benjamin"
  }
}
```

May correct: name, dates, duration, format, classification, status, URLs, location, course variants, review decisions, editorial profile. May not: change ids or provenance, invent events, add/remove course variants. `docs/CURATED_DATA.md` has the full file format and rules.

## Data sources

Four implemented (verified details and limitations per source in `docs/DATA_SOURCES.md`):

| Source | Priority | What it provides | Parsing |
| --- | --- | --- | --- |
| `radsport-events` | specialist_aggregator | DE-heavy, all disciplines; good for ultras/stage races | hand-written JSON API adapter |
| `bawue-radsportverband` | official_federation | BAD/WTB season PDF; no distances/coords | hand-written PDF adapter |
| `epic-series` | official_organizer | Swiss Epic, Cape Epic & co (aggregators miss them) | hand-written HTML adapter |
| `ffc` | official_federation | French Randonnée VTT calendar | **generic LLM extraction** (no site-specific parsing) |

New sources from here on follow the generic pattern: a `SourceProfile` + a `pages()` navigation method; extraction, validation and provenance are shared.

## Plan and status

`docs/PLAN.md` holds the roadmap, the data-treatment contract, and phase status (Phases 0–3 complete: pipeline, classification, website; Phase 4 discovery implemented as the `discover` command).

## Development

Python dependencies and the virtual environment are managed with [uv](https://docs.astral.sh/uv/). The repo root is a uv workspace; `services/pipeline` is the only Python member, and `.venv` lives at the root.

```sh
uv sync
```

Run the pipeline checks:

```sh
uv run ruff check
uv run pytest -q services/pipeline/tests
```

Local development uses SQLite so the pipeline runs with no external services. Production persistence is planned to move to PostgreSQL with Alembic migrations, behind the same repository layer.

## Repository layout

```
mtb-marathon-calendar/
├── apps/
│   └── site/            # Static Astro public site
├── services/
│   └── pipeline/        # Python data pipeline CLI (collect/enrich/discover/export)
├── data/
│   ├── generated/       # Machine artifact: events.json, events.ics, per-event .ics
│   └── curated/         # Human overlays (committed to Git)
├── docs/                # PLAN, DATA_SOURCES, CURATED_DATA
└── raw/                 # Local raw source snapshots (gitignored)
```
