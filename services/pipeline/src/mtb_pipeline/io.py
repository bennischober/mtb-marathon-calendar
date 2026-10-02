import json
from datetime import UTC, datetime
from pathlib import Path

from .models import Edition


def load_editions(path: Path) -> list[Edition]:
    """Load editions from either a bare array or a generated export envelope.

    Fixtures are stored as a plain array of editions, while `export` wraps them
    in an object with `schema_version` and `generated_at`. Accepting both keeps
    `export` output re-ingestable and re-validatable.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("events")
    if not isinstance(payload, list):
        raise ValueError("input must be a JSON array of editions or an export object")
    return [Edition.model_validate(item) for item in payload]


def build_export(editions: list[Edition], generated_at: datetime | None = None) -> dict:
    timestamp = generated_at or datetime.now(UTC)
    sorted_editions = sorted(editions, key=lambda item: (item.date_start, item.id))
    return {
        "schema_version": "1.0",
        "generated_at": timestamp.isoformat(),
        "events": [_dump_edition(edition) for edition in sorted_editions],
    }


def _dump_edition(edition: Edition) -> dict:
    payload = edition.model_dump(mode="json", exclude_none=True)
    if not payload.get("extraction_warnings"):
        payload.pop("extraction_warnings", None)
    return payload


def write_export(editions: list[Edition], path: Path, generated_at: datetime | None = None) -> dict:
    payload = build_export(editions, generated_at=generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
