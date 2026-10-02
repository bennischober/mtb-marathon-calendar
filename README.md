# MTB Marathon Race Calendar

An automated-first calendar that aggregates publicly available information about mountain bike marathon races in **Germany, Austria, Switzerland, Italy, and France** — including one-day ultra marathons and multi-day stage/epic races.

> **Scope decision:** This is a discovery and comparison layer, not a replacement for official race pages or registration systems. We exclude UCI World Championship / World Series premium events from the default view and focus on open/citizen races that are scattered across organizer sites, federation lists, event platforms, and the wider web.

## Goal

A continuously refreshed, provenance-aware dataset built primarily through automated ingestion:
1. **Official feeds and calendars** (organizers, federations, series)
2. **Public event platforms, sitemaps, and web scraping**
3. **Automated web discovery** through approved search APIs and public feeds
4. **Submissions and exception review** only as a fallback for gaps automation cannot reach

The website links out to the original event and registration pages; it does not handle registration itself.

The core calendar will prioritize automated event/date/location data. Later iterations can add course variants, previous editions, route/area details, editorial race profiles, community information, and human review/false-positive annotations.

This is a permanent monorepo: `apps/site` is the Astro frontend, `services/pipeline` is the Python data pipeline, and `packages/schema` defines their shared data contract.

## Plan

See `docs/PLAN.md` for the full roadmap and `docs/DATA_SOURCES.md` for the research on where to get data.

## Development

Python dependencies and the virtual environment are managed with [uv](https://docs.astral.sh/uv/). The repo root is a uv workspace; `services/pipeline` is the only Python member, and `.venv` lives at the root.

```sh
uv sync
```

`uv sync` installs the project plus the `dev` dependency group (pytest, ruff). The pinned interpreter version is in `.python-version`, and resolved dependency versions are locked in `uv.lock`.

Run the pipeline checks:

```sh
uv run ruff check
uv run ruff format --check
uv run pytest -q services/pipeline/tests
```

Validate and export a fixture without touching the database:

```sh
uv run mtb-pipeline validate --input services/pipeline/tests/fixtures/events.json
uv run mtb-pipeline export --input services/pipeline/tests/fixtures/events.json --output data/generated/events.json
```

Use the SQLite database for ingestion and site exports (default path: `data/mtb.sqlite3`):

```sh
uv run mtb-pipeline init-db
uv run mtb-pipeline ingest --input services/pipeline/tests/fixtures/events.json
uv run mtb-pipeline export --output data/generated/events.json
```

Pass `--database <path>` to any of those commands to target a different database file. For `export`, `--input` and `--database` are mutually exclusive. Ingestion is idempotent: re-running it updates existing editions, course variants, and sources in place.

Collect from a live source through the polite fetcher:

```sh
uv run mtb-pipeline collect --source radsport-events --dry-run
uv run mtb-pipeline collect --source radsport-events --delay 2
uv run mtb-pipeline collect --source radsport-events --country DE --country AT --limit 100
```

`collect` fetches every page the source needs, stores an untimestamped-by-policy
raw snapshot under `raw/<source>/`, filters by scope country and the shared
classifier, then writes the survivors to SQLite. `--dry-run` performs the full
fetch, parse, and classification but writes nothing to the database.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Fetch and classify, but do not write to the database |
| `--delay <seconds>` | Minimum delay between requests to the same host |
| `--limit <n>` | Stop after roughly `n` records instead of paging the whole source |
| `--country <CC>` | Repeatable scope filter; defaults to DE, AT, CH, IT, FR |
| `--raw-dir <path>` | Snapshot directory, defaults to `raw/` |

The fetcher honours `robots.txt`, retries transient failures, sends a
descriptive User-Agent, and stores every response body together with a
`content_hash`, the retrieval timestamp, and the URL that produced it, so a
parsing change can be replayed against the exact bytes later. Set a real contact
address in `DEFAULT_USER_AGENT` in `services/pipeline/src/mtb_pipeline/http_client.py`
before scheduling regular collection.

Classification is LLM-primary: adapters only extract facts, then the model reads
each record's title, description and distances and assigns the final class
(`mtb_marathon`, `mtb_ultra`, `mtb_stage_race`, `mtb_point_to_point`, or
`exclude`). Verdicts are cached, validated, and recorded as `llm:<reason>`
warnings, so every automated decision stays auditable. Without `MTB_LLM_API_KEY`
the pipeline still runs — records remain `mtb_candidate` and appear on the site
as unreviewed rather than being dropped.

Every `collect` run appends a row to `collection_runs`, including failures, so
the calendar can report on itself instead of quietly going stale:

```sh
uv run mtb-pipeline status
uv run mtb-pipeline status --json
```

The report pairs each source's declared metadata (priority, cadence, format,
terms status) with what actually happened: last success, age against the
expected cadence, failure rate, and how many records the last run kept.
Sources are marked `ok`, `stale`, `failing`, or `never_run`, worst first.

Two sources are implemented: `radsport-events` (a specialist aggregator) and
`bawue-radsportverband` (an official regional federation calendar, parsed from its
season PDF). Because they carry different source priorities, running both into one
database lets `export` merge overlapping races and report disagreements. See
`docs/DATA_SOURCES.md` for the verified findings and known limitations of each.

When two sources describe the same race, `export` picks a value per field by
source priority and stores every disagreement in the `merge_conflicts` table, so
the decision can be checked instead of trusted:

```sh
uv run mtb-pipeline conflicts
uv run mtb-pipeline conflicts --json
uv run mtb-pipeline conflicts --edition <edition-id>
```

The table describes the most recent export rather than accumulating history, so a
field that stopped disagreeing stops being listed. Fixture exports (`--input`)
have no database to write to and only print conflicts to stdout.

## Curated corrections

`export` merges human-written overlays from `data/curated/` on top of the
machine data, so scheduled ingestion can never overwrite a review decision or a
corrected field:

```sh
uv run mtb-pipeline export                      # machine data + curated overlays
uv run mtb-pipeline export --no-curated         # machine data only
```

An overlay may correct fields, attach an editorial profile, and record a review
decision. It cannot invent an event, change an id, or edit provenance, because
those are evidence rather than opinion. See `docs/CURATED_DATA.md`.

Build the site:

```sh
cd apps/site && npm install && npm run build
```

Local development uses SQLite so the pipeline runs with no external services. Production persistence is planned to move to PostgreSQL with Alembic migrations, behind the same repository layer.

## Scheduled collection

`.github/workflows/collect.yml` runs every registered source once a week, merges
the results with deduplication and the curated overlays, validates the result,
builds the site and can deploy it to GitHub Pages. It can also be started by hand
from the Actions tab.

Two repository variables gate it, and both are opt-in so that merging the
workflow cannot cause unattended traffic by accident:

| Variable | Effect |
| --- | --- |
| `CONTACT_USER_AGENT` | **Required.** The user agent used for scheduled requests. |
| `PAGES_DEPLOY_ENABLED` | Set to `true` to allow deploying to GitHub Pages. |

`CONTACT_USER_AGENT` must contain a real contact address. The workflow refuses to
start if it is unset or still contains the placeholder `example.org`, because a
scheduled crawler that cannot be contacted is exactly the kind of traffic a source
operator cannot respond to. Set it in
*Settings → Secrets and variables → Actions → Variables*.

Between those gates the workflow still fails loudly rather than publishing
rubbish. It runs the test suite before touching a source, tolerates one source
being unavailable but reports it, and rejects an artifact that lost most of its
records, which is what a broken parser or a quietly renamed calendar file looks
like. Every run appends the `status` report and the export log to the job summary.

## Repository layout (planned)

```
mtb-marathon-calendar/
├── apps/
│   └── site/            # Static Astro public site
├── services/
│   └── pipeline/        # Python data collection and analysis CLI
├── packages/
│   └── schema/          # Shared JSON Schema and generated types
├── db/                  # Migrations and repository queries
├── data/
│   ├── generated/       # Machine-generated JSON for the site
│   └── curated/         # Human review and editorial overlays
├── raw/                 # Local source snapshots; keep out of Git
├── infra/               # Containers, deployment, and operations
└── .github/workflows/   # Scheduled ingestion and deployment
```
