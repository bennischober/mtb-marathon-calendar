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

Only `radsport-events` is implemented so far; see `docs/DATA_SOURCES.md` for the
verified findings and known limitations of that source.

Build the site:

```sh
cd apps/site && npm install && npm run build
```

Local development uses SQLite so the pipeline runs with no external services. Production persistence is planned to move to PostgreSQL with Alembic migrations, behind the same repository layer.

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
