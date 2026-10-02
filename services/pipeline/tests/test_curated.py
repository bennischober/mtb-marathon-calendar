"""Curated overlay merge.

The point of these tests is the guarantee that a collection run cannot erase a
human decision, and the limits on what an overlay is allowed to do.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mtb_pipeline.curated import (
    OverlayError,
    load_overlays,
    merge,
)
from mtb_pipeline.io import build_published_export, load_editions
from mtb_pipeline.models import Edition

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "events.json"


def editions_payload() -> list[dict]:
    editions = load_editions(FIXTURE)
    return [
        json.loads(json.dumps(edition.model_dump(mode="json", exclude_none=True)))
        for edition in editions
    ]


def write_overlay(directory: Path, name: str, entry: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(entry, indent=2), encoding="utf-8")


def test_no_overlays_leaves_generated_data_untouched():
    events = editions_payload()

    merged, applied = merge(events, {})

    assert merged == events
    assert applied == []


def test_a_corrected_field_overrides_the_generated_value():
    from mtb_pipeline.curated import CuratedOverlay

    events = editions_payload()
    target = events[0]["id"]
    original_name = events[0]["name"]

    merged, applied = merge(
        events,
        {target: CuratedOverlay(edition_id=target, fields={"name": "Corrected Name"})},
    )

    assert merged[0]["name"] == "Corrected Name"
    assert merged[0]["name"] != original_name
    assert any("corrected name" in line for line in applied)


def test_overlays_survive_a_regenerated_artifact(tmp_path):
    """The core guarantee: re-running collection must not undo a human decision."""
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)
    target = editions[0].id

    write_overlay(
        curated,
        "review.json",
        {
            "edition_id": target,
            "name": "Human Corrected Name",
            "review": {"status": "approved", "reviewed_by": "reviewer-1"},
            "editorial": {"summary": "A hand-written profile."},
        },
    )

    # Simulate a later ingestion run re-deriving the edition from scratch.
    regenerated = editions_payload()
    regenerated[0]["name"] = "Machine Name Again"
    reingested = [Edition.model_validate(event) for event in regenerated]

    payload, applied = build_published_export(reingested, generated_at=NOW, curated_dir=curated)

    published = payload["events"][0]
    assert published["name"] == "Human Corrected Name"
    assert published["review"]["status"] == "approved"
    assert published["editorial_profile"]["summary"] == "A hand-written profile."
    assert applied


def test_an_overlay_cannot_change_identity_or_provenance(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)

    write_overlay(
        curated,
        "bad.json",
        {
            "edition_id": editions[0].id,
            "id": "something-else",
            "classification_confidence": 1.0,
            "source_refs": [{"url": "https://fabricated.example"}],
        },
    )

    with pytest.raises(OverlayError, match="may not change"):
        build_published_export(editions, generated_at=NOW, curated_dir=curated)


def test_an_overlay_cannot_invent_an_event(tmp_path):
    """A record automation never found has no provenance to attach an opinion to."""
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)

    write_overlay(
        curated,
        "ghost.json",
        {
            "edition_id": "totally-made-up-2099",
            "review": {"status": "approved"},
        },
    )

    payload, applied = build_published_export(editions, generated_at=NOW, curated_dir=curated)

    assert [event["id"] for event in payload["events"]] == [e.id for e in editions]
    assert any("unknown edition" in line for line in applied)


def test_a_stale_overlay_is_reported_not_silently_ignored(tmp_path):
    curated = tmp_path / "curated"
    write_overlay(curated, "stale.json", {"edition_id": "renamed-away-2026"})

    _, applied = build_published_export(
        load_editions(FIXTURE), generated_at=NOW, curated_dir=curated
    )

    assert any("unknown edition 'renamed-away-2026'" in line for line in applied)


def test_review_defaults_to_the_publish_timestamp(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)

    write_overlay(
        curated,
        "review.json",
        {
            "edition_id": editions[0].id,
            "review": {"status": "false_positive", "reason": "Cancelled, not a race."},
        },
    )

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)

    assert payload["events"][0]["review"]["reviewed_at"] == NOW.isoformat()


def test_an_explicit_review_timestamp_is_kept(tmp_path):
    curated = tmp_path / "curated"
    curated.mkdir(parents=True, exist_ok=True)
    editions = load_editions(FIXTURE)
    reviewed_at = "2026-09-30T08:00:00+00:00"

    write_overlay(
        curated,
        "review.json",
        {
            "edition_id": editions[0].id,
            "review": {"status": "approved", "reviewed_at": reviewed_at},
        },
    )

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)
    published_at = payload["events"][0]["review"]["reviewed_at"]

    # The reviewer's own timestamp wins; only the missing one falls back to NOW.
    assert datetime.fromisoformat(published_at) == datetime.fromisoformat(reviewed_at)
    assert published_at != NOW.isoformat()


def test_location_is_merged_field_by_field(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)
    original_region = editions[0].location.region

    write_overlay(
        curated,
        "geo.json",
        {
            "edition_id": editions[0].id,
            "location": {"region": "Bavaria", "lat": 47.5},
        },
    )

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)
    location = payload["events"][0]["location"]

    assert location["region"] == "Bavaria"
    assert location["lat"] == 47.5
    assert location["country"] == editions[0].location.country
    assert original_region == location["region"] or original_region != "Bavaria"


def test_course_variants_are_corrected_in_place(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)
    variant_id = editions[0].course_variants[0].id

    write_overlay(
        curated,
        "course.json",
        {
            "edition_id": editions[0].id,
            "course_variants": [{"id": variant_id, "elevation_m": 4321}],
        },
    )

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)
    variants = payload["events"][0]["course_variants"]

    assert len(variants) == len(editions[0].course_variants)
    assert next(v for v in variants if v["id"] == variant_id)["elevation_m"] == 4321


def test_an_overlay_cannot_add_a_course_variant(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)

    write_overlay(
        curated,
        "course.json",
        {
            "edition_id": editions[0].id,
            "course_variants": [{"id": "not-a-real-variant", "distance_km": 999}],
        },
    )

    with pytest.raises(OverlayError, match="is not on this edition"):
        build_published_export(editions, generated_at=NOW, curated_dir=curated)


def test_later_files_override_earlier_ones(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)
    target = editions[0].id

    write_overlay(curated, "a-first.json", {"edition_id": target, "name": "First"})
    write_overlay(curated, "b-second.json", {"edition_id": target, "name": "Second"})

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)

    assert payload["events"][0]["name"] == "Second"


def test_a_missing_curated_directory_is_not_an_error(tmp_path):
    payload, applied = build_published_export(
        load_editions(FIXTURE), generated_at=NOW, curated_dir=tmp_path / "nope"
    )

    assert len(payload["events"]) == 2
    assert applied == []


def test_overlays_may_be_written_as_a_list(tmp_path):
    curated = tmp_path / "curated"
    curated.mkdir(parents=True, exist_ok=True)
    editions = load_editions(FIXTURE)
    (curated / "many.json").write_text(
        json.dumps(
            [
                {"edition_id": editions[0].id, "name": "A"},
                {"edition_id": editions[1].id, "name": "B"},
            ]
        ),
        encoding="utf-8",
    )

    payload, _ = build_published_export(editions, generated_at=NOW, curated_dir=curated)
    names = {event["name"] for event in payload["events"]}

    assert names == {"A", "B"}


def test_an_invalid_review_status_is_rejected(tmp_path):
    curated = tmp_path / "curated"
    editions = load_editions(FIXTURE)

    write_overlay(
        curated,
        "bad.json",
        {
            "edition_id": editions[0].id,
            "review": {"status": "definitely_fine"},
        },
    )

    with pytest.raises(OverlayError, match="invalid review"):
        build_published_export(editions, generated_at=NOW, curated_dir=curated)


def test_an_overlay_without_an_edition_id_is_rejected(tmp_path):
    curated = tmp_path / "curated"
    write_overlay(curated, "bad.json", {"name": "Nameless"})

    with pytest.raises(OverlayError, match="edition_id"):
        load_overlays(curated)
