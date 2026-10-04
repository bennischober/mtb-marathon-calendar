import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .collect import collect, summarize
from .config import ConfigError, load_pipeline_config
from .conflicts import load_merge_conflicts, record_merge_conflicts
from .database import (
    SchemaOutOfDate,
    create_db_engine,
    init_db,
    session_scope,
    verify_schema,
)
from .discovery import StageracesDiscovery, run_discovery
from .enrich import enrich_editions
from .http_client import DEFAULT_USER_AGENT, Fetcher, FetchError, RobotsDisallowed
from .io import build_ics, build_published_export, load_editions
from .llm import LLMClassifier, LLMConfig
from .llm_cache import LLMCacheRecord  # noqa: F401 — registers the ORM table
from .orm import EditionRecord
from .repository import load_editions as load_editions_from_db
from .repository import prune_stale_editions, upsert_editions
from .runs import RunOutcome, record_run, source_health
from .sources import CollectOptions, all_profiles, available_sources, get_adapter
from .sources.radsport_events import SUPPORTED_COUNTRIES

DEFAULT_DATABASE = Path("data/mtb.sqlite3")
DEFAULT_EXPORT = Path("data/generated/events.json")
DEFAULT_CURATED_DIR = Path("data/curated")
DEFAULT_RAW_DIR = Path("raw")
DEFAULT_LLM_CACHE = Path("data/llm-cache.sqlite3")

app = typer.Typer(no_args_is_help=True, help="Collect, validate, and export MTB marathon events.")


def parse_generated_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    # Database timestamps are always UTC-aware; a naive --now value would
    # crash the subtraction in SourceHealth.age, so assume UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def read_database(database: Path):
    engine = create_db_engine(database)
    init_db(engine)
    verify_schema(engine)
    try:
        with session_scope(engine) as session:
            return load_editions_from_db(session)
    finally:
        engine.dispose()


@app.command("collect")
def collect_command(
    source: str = typer.Option(
        ...,
        "--source",
        help=f"Source adapter to run. Available: {', '.join(available_sources())}",
    ),
    database: Path | None = typer.Option(
        None,
        "--database",
        help="SQLite database path; defaults to data/mtb.sqlite3.",
    ),
    raw_dir: Path = typer.Option(
        DEFAULT_RAW_DIR,
        "--raw-dir",
        help="Directory for raw source snapshots.",
    ),
    country: list[str] = typer.Option(
        list(SUPPORTED_COUNTRIES),
        "--country",
        help="Restrict to these ISO country codes. Repeatable.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and parse without writing to the database.",
    ),
    user_agent: str = typer.Option(
        DEFAULT_USER_AGENT,
        "--user-agent",
        help="User agent sent to the source. Please set a real contact address.",
    ),
    delay_seconds: float = typer.Option(
        1.0,
        "--delay",
        min=0.0,
        help="Minimum delay between HTTP requests, in seconds.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Stop after roughly this many source records.",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help=(
            "OpenAI-compatible model used to classify records (e.g. "
            "gpt-4o-mini). Overrides the config file; without a model from "
            "either source the run stays in degraded candidate mode."
        ),
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="OpenAI-compatible API base URL; overrides the config file.",
    ),
    llm_cache: Path = typer.Option(
        DEFAULT_LLM_CACHE,
        "--llm-cache",
        help="SQLite file caching LLM answers by content hash and model.",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        help="Pipeline config TOML with an [llm] section (base_url, model, api_key).",
    ),
) -> None:
    countries = tuple(code.upper() for code in country)
    unknown = set(countries) - set(SUPPORTED_COUNTRIES)
    if unknown:
        typer.echo(
            f"Unsupported countries: {', '.join(sorted(unknown))}. "
            f"Scope is {', '.join(SUPPORTED_COUNTRIES)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        adapter = get_adapter(source)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    started_at = datetime.now(UTC)

    def record_success(result) -> None:
        if dry_run:
            return
        _persist(  # noqa: ANN001
            database or DEFAULT_DATABASE,
            editions=result.editions,
            run=_RunToRecord(
                adapter=adapter.name,
                started_at=started_at,
                requests=result.requests,
                documents=result.documents,
                parsed=result.parsed,
                kept=result.kept,
                excluded=result.excluded,
                candidates=result.candidates,
            ),
        )

    def record_failure(error: Exception) -> None:
        if dry_run:
            return
        # A failed run is still worth recording, otherwise a source that broke
        # last week looks identical to one that was never attempted.
        try:
            _persist(
                database or DEFAULT_DATABASE,
                editions=[],
                run=_RunToRecord(
                    adapter=adapter.name,
                    started_at=started_at,
                    error=str(error),
                ),
                only_run=True,
            )
        except (OSError, SQLAlchemyError, SchemaOutOfDate):
            # Never let bookkeeping hide the original collection error.
            pass

    try:
        pipeline_config = load_pipeline_config(config_path)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    llm = pipeline_config.llm
    classifier = _build_classifier(
        model=llm_model if llm_model is not None else (llm.model if llm else None),
        base_url=llm_base_url if llm_base_url is not None else (
            llm.base_url if llm else "https://api.openai.com/v1"
        ),
        api_key=llm.api_key if llm else None,
    )
    if classifier is not None:
        # The status line names the model so scheduled-run logs show what
        # classified the data; the key is never echoed.
        typer.echo(
            f"  llm: classifying with {classifier._config.model}"
            f" via {classifier._config.base_url}"
        )

    try:
        with Fetcher(
            user_agent=user_agent,
            delay_seconds=delay_seconds,
            raw_dir=raw_dir,
        ) as fetcher:
            result = collect(
                adapter,
                countries=countries,
                options=CollectOptions(limit=limit),
                fetcher=fetcher,
                on_finished=record_success,
                on_error=record_failure,
                classifier=classifier,
                classifier_cache=llm_cache,
            )
    except RobotsDisallowed as exc:
        # `on_error` already recorded the failed run.
        typer.echo(f"Collection stopped: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (FetchError, OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Collection failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(summarize(result))

    if dry_run:
        typer.echo("Dry run: nothing written to the database.")
        return

    database_path = database or DEFAULT_DATABASE
    typer.echo(f"Stored {len(result.editions)} event editions in {database_path}.")


class _RunToRecord:
    """Bundle of run counters, written together with the editions."""

    def __init__(
        self,
        *,
        adapter: str,
        started_at: datetime,
        error: str | None = None,
        requests: int = 0,
        documents: int = 0,
        parsed: int = 0,
        kept: int = 0,
        excluded: int = 0,
        candidates: int = 0,
    ) -> None:
        self.adapter = adapter
        self.started_at = started_at
        self.outcome = RunOutcome(
            ok=error is None,
            error=error,
            requests=requests,
            documents=documents,
            parsed=parsed,
            kept=kept,
            excluded=excluded,
            candidates=candidates,
        )


def _build_classifier(
    model: str | None,
    base_url: str,
    api_key: str | None,
) -> LLMClassifier | None:
    """Build the LLM classifier from already-resolved settings.

    Precedence (CLI > env > config file) is applied by the caller; a missing
    key or model means the run stays in degraded candidate mode.
    """
    if not model or not api_key:
        return None
    return LLMClassifier(
        LLMConfig(base_url=base_url.rstrip("/"), model=model, api_key=api_key)
    )


def _persist(
    database_path: Path,
    *,
    editions: list,
    run: _RunToRecord,
    only_run: bool = False,
) -> None:
    """Write editions and the run record in a single transaction."""
    engine = create_db_engine(database_path)
    init_db(engine)
    verify_schema(engine)
    try:
        with session_scope(engine) as session:
            with session.begin():
                if not only_run:
                    upsert_editions(session, editions)
                    pruned = prune_stale_editions(
                        session,
                        run.adapter,
                        [edition.id for edition in editions],
                    )
                else:
                    pruned = 0
                record_run(session, run.adapter, started_at=run.started_at, outcome=run.outcome)
        if pruned:
            typer.echo(f"  pruned {pruned} stale edition(s) no longer produced by {run.adapter}")
    finally:
        engine.dispose()


@app.command()
def status(
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a table.",
    ),
    now: str | None = typer.Option(
        None,
        "--now",
        help="ISO timestamp used as the evaluation clock; defaults to the current time.",
    ),
) -> None:
    """Report registry metadata and observed health for every source."""
    try:
        engine = create_db_engine(database)
        init_db(engine)
        verify_schema(engine)
        try:
            with session_scope(engine) as session:
                report = source_health(session, all_profiles(), now=parse_generated_at(now))
        finally:
            engine.dispose()
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Status failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps([item.row() for item in report], indent=2, default=str))
        return

    typer.echo(
        f"{'SOURCE':22} {'STATE':10} {'PRIORITY':22} {'RUNS':>5} {'FAIL%':>6} "
        f"{'AGE_H':>7} {'KEPT':>5} {'CADENCE':>8}"
    )
    for item in report:
        row = item.row()
        failure = "n/a" if row["failure_rate"] is None else f"{row['failure_rate'] * 100:.0f}"
        age = "n/a" if row["age_hours"] is None else f"{row['age_hours']:.1f}"
        kept = "n/a" if row["kept_last"] is None else str(row["kept_last"])
        typer.echo(
            f"{row['source']:22} {row['state']:10} {row['priority']:22} "
            f"{row['runs']:>5} {failure:>6} {age:>7} {kept:>5} "
            f"{row['cadence_hours']:>7}h"
        )
        if row["state"] in {"failing", "stale"} or row["terms"] != "confirmed":
            typer.echo(f"{'':22} terms={row['terms']} robots={row['robots']}")


def user_agent_default() -> str:
    return DEFAULT_USER_AGENT


@app.command()
def enrich(
    database: Path | None = typer.Option(
        None,
        "--database",
        help="SQLite database path; defaults to data/mtb.sqlite3.",
    ),
    max_races: int | None = typer.Option(
        None,
        "--max-races",
        min=1,
        help="Enrich only the first N races (for trials).",
    ),
) -> None:
    """Read organizers' own pages and add descriptions, prices, courses.

    Additive and provenance-kept: fields the organizer publishes are merged
    into the stored editions; aggregator facts are only replaced where the
    organizer contradicts them. A rich detail page is the goal; nothing is
    invented.
    """
    database_path = database or DEFAULT_DATABASE
    try:
        editions = read_database(database_path)
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Enrich failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    config = load_pipeline_config()
    if config.llm is None:
        typer.echo("Enrich needs the LLM (config file or MTB_LLM_* env).", err=True)
        raise typer.Exit(code=1)
    classifier = LLMClassifier(config.llm)

    targets = editions if max_races is None else editions[:max_races]
    with Fetcher(user_agent=user_agent_default(), delay_seconds=1.5) as fetcher:
        try:
            result = enrich_editions(targets, fetcher, classifier)
        except Exception as exc:
            typer.echo(f"Enrich failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        finally:
            classifier.close()

    try:
        engine = create_db_engine(database_path)
        init_db(engine)
        verify_schema(engine)
        with session_scope(engine) as session:
            with session.begin():
                upsert_editions(session, result.editions)
    finally:
        engine.dispose()

    for line in result.summary_lines():
        typer.echo(line)


@app.command()
def discover(
    max_candidates: int | None = typer.Option(
        None,
        "--max-candidates",
        min=1,
        help="Cap how many candidates are crawled (for quick trials).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the machine-readable report instead of text.",
    ),
) -> None:
    """Crawl discovery sources and propose in-scope races we do not have yet.

    Proposals are a report, never imports: a human decides what enters the
    calendar, and which source should cover it properly.
    """
    config = load_pipeline_config()
    if config.llm is None:
        typer.echo(
            "Discovery needs the LLM (config file or MTB_LLM_* env).",
            err=True,
        )
        raise typer.Exit(code=1)

    classifier = LLMClassifier(config.llm)
    try:
        known = {edition.name for edition in read_database(DEFAULT_DATABASE)}
        result = run_discovery(
            StageracesDiscovery(),
            classifier,
            known_names=known,
            max_candidates=max_candidates,
        )
    except Exception as exc:
        typer.echo(f"Discovery failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        classifier.close()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "candidates": len(result.candidates),
                    "evaluated": result.evaluated,
                    "proposals": [
                        {
                            "name": c.name,
                            "url": c.url,
                            "country": c.country,
                            "reason": (c.reason or "").replace(" [new]", ""),
                        }
                        for c in result.in_scope
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for line in result.summary_lines():
        typer.echo(line)


@app.command()
def conflicts(
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
    edition: str | None = typer.Option(
        None,
        "--edition",
        help="Show conflicts for one edition id only.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of conflicts to show.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of text.",
    ),
) -> None:
    """List fields where sources disagreed during the last export.

    Every conflict was already resolved automatically, by source priority.
    This command exists so the decision can be checked rather than trusted.
    """
    try:
        engine = create_db_engine(database)
        init_db(engine)
        verify_schema(engine)
        try:
            with session_scope(engine) as session:
                rows = load_merge_conflicts(
                    session, edition_id=edition, limit=limit if limit > 0 else None
                )
                names = _edition_names(session, [row.edition_id for row in rows])
        finally:
            engine.dispose()
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Conflicts lookup failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "edition_id": row.edition_id,
                        "name": names.get(row.edition_id),
                        "field": row.field,
                        "kept": row.kept,
                        "kept_source": row.kept_source,
                        "discarded": row.discarded,
                        "reason": row.reason,
                        "recorded_at": row.recorded_at,
                    }
                    for row in rows
                ],
                indent=2,
                default=str,
            )
        )
        return

    if not rows:
        typer.echo(
            "No merge conflicts recorded. Run `mtb-pipeline export` against a database first."
        )
        return

    typer.echo(f"{'EDITION':44} {'FIELD':22} {'KEPT FROM':22} REASON")
    for row in rows:
        label = names.get(row.edition_id) or row.edition_id
        typer.echo(f"{label[:44]:44} {row.field[:22]:22} {row.kept_source[:22]:22} {row.reason}")
        discarded = ", ".join(f"{key}={value}" for key, value in row.discarded.items())
        if discarded:
            typer.echo(f"{'':44} discarded: {discarded}")


def _edition_names(session: Session, edition_ids: list[str]) -> dict[str, str]:
    """Map edition ids to race names, for readable conflict output."""
    unique = {edition_id for edition_id in edition_ids}
    if not unique:
        return {}
    rows = session.execute(
        select(EditionRecord.id, EditionRecord.name).where(EditionRecord.id.in_(unique))
    ).all()
    return dict(rows)


@app.command()
def validate(
    input_path: Path = typer.Option(
        ...,
        "--input",
        exists=True,
        readable=True,
        help="JSON file containing event editions.",
    ),
) -> None:
    try:
        editions = load_editions(input_path)
    except (OSError, ValueError) as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Validated {len(editions)} event editions.")


@app.command("init-db")
def init_db_command(
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Drop existing tables first. Required after ORM model changes.",
    ),
) -> None:
    try:
        engine = create_db_engine(database)
        init_db(engine, reset=reset)
        verify_schema(engine)
        engine.dispose()
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Database initialization failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    action = "Re-initialized" if reset else "Initialized"
    typer.echo(f"{action} SQLite database at {database}.")


@app.command()
def ingest(
    input_path: Path = typer.Option(
        ...,
        "--input",
        exists=True,
        readable=True,
        help="JSON file containing event editions.",
    ),
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
) -> None:
    try:
        editions = load_editions(input_path)
        engine = create_db_engine(database)
        init_db(engine)
        verify_schema(engine)
        try:
            with session_scope(engine) as session:
                with session.begin():
                    upsert_editions(session, editions)
        finally:
            engine.dispose()
    except (OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ingested {len(editions)} event editions into {database}.")


@app.command()
def export(
    input_path: Path | None = typer.Option(
        None,
        "--input",
        help="Optional JSON input for fixture-based export.",
    ),
    database: Path | None = typer.Option(
        None,
        "--database",
        help="SQLite database path; defaults to data/mtb.sqlite3.",
    ),
    output_path: Path = typer.Option(
        DEFAULT_EXPORT,
        "--output",
        help="Destination for the site-facing JSON artifact.",
    ),
    generated_at: str | None = typer.Option(
        None,
        "--generated-at",
        help="Optional ISO timestamp used for reproducible exports.",
    ),
    curated_dir: Path | None = typer.Option(
        None,
        "--curated",
        help=f"Directory of human-written overlays; defaults to {DEFAULT_CURATED_DIR}.",
    ),
    no_curated: bool = typer.Option(
        False,
        "--no-curated",
        help="Skip curated overlays. Intended for regenerating the raw machine artifact.",
    ),
) -> None:
    if input_path is not None and database is not None:
        typer.echo("Use either --input or --database, not both.", err=True)
        raise typer.Exit(code=1)

    curated_path = None if no_curated else (curated_dir or DEFAULT_CURATED_DIR)

    try:
        if input_path is not None:
            editions = load_editions(input_path)
            database_path = None
        else:
            database_path = database or DEFAULT_DATABASE
            if str(database_path) != ":memory:" and not database_path.exists():
                raise ValueError(
                    f"database does not exist: {database_path}; run ingest or init-db first"
                )
            editions = read_database(database_path)

        payload, applied, conflicts = _publish(
            editions, output_path, curated_path, generated_at
        )
        if database_path is not None:
            stored = _store_conflicts(database_path, payload, conflicts, generated_at)
            if stored:
                typer.echo(f"  recorded {stored} merge conflict(s) for review")
    except (OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for line in applied:
        typer.echo(f"  {line}")
    typer.echo(f"Exported {len(payload['events'])} event editions to {output_path}.")


def _publish(
    editions: list,
    output_path: Path,
    curated_path: Path | None,
    generated_at: str | None,
) -> tuple[dict, list[str], list[dict]]:
    conflicts: list[dict] = []
    payload, applied = build_published_export(
        editions,
        parse_generated_at(generated_at),
        curated_dir=curated_path,
        conflicts=conflicts,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ics_path = output_path.with_suffix(".ics")
    ics_path.write_text(build_ics(editions), encoding="utf-8")
    _write_per_event_ics(payload, output_path)
    return payload, applied, conflicts


def _write_per_event_ics(payload: dict, output_path: Path) -> None:
    """One .ics per edition, next to the artifact, for the detail pages'
    Calendar buttons: ``/events/<slug>/<year>.ics`` matches the site routes."""
    from .io import _series_slug
    from .io import build_ics as _build

    events_dir = output_path.parent / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    from .models import Edition

    for edition in payload["events"]:
        target = events_dir / f"{_series_slug(edition['event_id'])}-{edition['edition']}.ics"
        try:
            model = Edition.model_validate(edition)
        except Exception:
            continue
        target.write_text(_build([model]), encoding="utf-8")


def _store_conflicts(
    database_path: Path,
    payload: dict,
    conflicts: list[dict],
    generated_at: str | None,
) -> int:
    """Persist this export's conflicts so they can be inspected later.

    Skipped for fixture exports, which have no database to write to.
    """
    engine = create_db_engine(database_path)
    try:
        init_db(engine)
        verify_schema(engine)
        with session_scope(engine) as session:
            stored = record_merge_conflicts(
                session,
                conflicts,
                edition_ids=[edition["id"] for edition in payload["events"]],
                recorded_at=parse_generated_at(generated_at) or datetime.now(UTC),
            )
            session.commit()
    finally:
        engine.dispose()
    return stored


if __name__ == "__main__":
    app()
