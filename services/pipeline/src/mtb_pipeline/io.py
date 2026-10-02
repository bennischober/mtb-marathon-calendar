import json
from datetime import UTC, datetime
from pathlib import Path

from .models import Edition


def load_editions(path: Path) -> list[Edition]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("input must be a JSON array of editions")
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
