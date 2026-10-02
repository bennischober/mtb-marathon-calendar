"""Known-events registry: races we expect to see, so gaps become visible.

An aggregator being silent about a race (radsport-events has never listed the
Swiss Epic) looks identical to the race not existing. The registry flips that:
each entry names a race we believe exists, and the export reports any known
race that no collected source covered, instead of quietly missing it.

The registry is *not* a source of truth for record contents — it never
contributes fields to the export. It only names events that must show up from
some real source (or as an explicitly marked placeholder).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class KnownEvent(BaseModel):
    """One race we expect the pipeline to find somewhere."""

    #: stable identifier, namespaced like edition ids but not one
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    #: ISO country code the race is expected in
    country: str = Field(pattern=r"^[A-Z]{2}$")
    #: why we believe this exists (URL, past edition, news)
    evidence: str = Field(min_length=1)
    #: when the race typically happens, for the gap report
    typical_month: str | None = None


class RegistryReport:
    """Outcome of checking collected editions against the registry."""

    def __init__(
        self,
        missing: list[KnownEvent],
        matched: list[KnownEvent],
    ) -> None:
        self.missing = missing
        self.matched = matched

    def log_lines(self) -> list[str]:
        lines = [f"known races matched: {len(self.matched)}"]
        for event in self.missing:
            when = f" (typically {event.typical_month})" if event.typical_month else ""
            lines.append(
                f"known race missing from all sources: {event.name} "
                f"({event.country}){when} — evidence: {event.evidence}"
            )
        return lines


def load_known_events(directory: Path) -> list[KnownEvent]:
    """Load ``known-events.json`` files from a curated directory."""
    events: list[KnownEvent] = []
    for path in sorted(directory.glob("known-events*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}: invalid JSON: {error}") from error
        items = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError(f"{path}: expected a JSON array or an object with 'events'")
        for item in items:
            try:
                events.append(KnownEvent.model_validate(item))
            except ValidationError as error:
                raise ValueError(f"{path}: invalid known event: {error}") from error
    return events


def check_known_events(
    known: list[KnownEvent], editions: list[dict[str, Any]]
) -> RegistryReport:
    """Match known races against collected editions by name tokens.

    Matching is deliberately loose (distinctive token containment): the
    registry says "some source must mention this race", not "this exact
    record is correct".
    """
    matched: list[KnownEvent] = []
    missing: list[KnownEvent] = []
    for event in known:
        distinctive = _distinctive_tokens(event.name)
        if not distinctive:
            # An entry whose name carries no identity ("Bike Marathon") can
            # match anything, so it is treated as unmatched rather than
            # silently matching every record.
            missing.append(event)
            continue
        hit = any(
            _distinctive_tokens(str(edition.get("name") or "")) >= distinctive
            and (edition.get("location") or {}).get("country") == event.country
            for edition in editions
        )
        (matched if hit else missing).append(event)
    return RegistryReport(missing=missing, matched=matched)


def _distinctive_tokens(name: str) -> set[str]:
    """Words that carry identity, ignoring generic race vocabulary."""
    generic = {"mtb", "bike", "bikes", "marathon", "race", "epic", "the", "festival"}
    return {
        token.lower().strip(".,-")
        for token in name.split()
        if token.lower().strip(".,-") not in generic and len(token) >= 3
    }
