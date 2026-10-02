"""Known-events registry: silent gaps become reported gaps."""

import json

import pytest

from mtb_pipeline.known_events import (
    KnownEvent,
    check_known_events,
    load_known_events,
)


def _event(name: str = "Swiss Epic", country: str = "CH") -> KnownEvent:
    return KnownEvent(
        id=f"known-{name.lower().replace(' ', '-')}",
        name=name,
        country=country,
        evidence="https://example.org/race",
    )


def test_a_missing_race_is_reported_not_swallowed():
    report = check_known_events([_event()], editions=[])

    assert report.matched == []
    assert len(report.missing) == 1
    assert "Swiss Epic" in report.log_lines()[1]


def test_a_collected_race_counts_as_matched_by_distinctive_token():
    edition = {
        "name": "SPAR Swiss Epic",
        "location": {"country": "CH"},
    }

    report = check_known_events([_event()], editions=[edition])

    assert report.missing == []
    assert len(report.matched) == 1


def test_the_same_name_in_the_wrong_country_is_still_missing():
    edition = {"name": "Swiss Epic", "location": {"country": "DE"}}

    report = check_known_events([_event()], editions=[edition])

    assert report.missing == [  # type: ignore[comparison-overlap]
        check_known_events([_event()], editions=[]).missing[0]
    ]


def test_generic_race_words_never_match_on_their_own():
    """A registry entry whose only tokens are generic must not match everything."""
    event = _event(name="Bike Marathon")
    edition = {"name": "Totally Different Race", "location": {"country": "CH"}}

    report = check_known_events([event], editions=[edition])

    assert report.matched == []


def test_overlays_are_loaded_from_known_events_files(tmp_path):
    directory = tmp_path / "curated"
    directory.mkdir()
    (directory / "known-events.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "known-x",
                        "name": "X Race",
                        "country": "DE",
                        "evidence": "https://x.example",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    events = load_known_events(directory)

    assert len(events) == 1
    assert events[0].name == "X Race"


def test_an_invalid_registry_entry_fails_loudly(tmp_path):
    directory = tmp_path / "curated"
    directory.mkdir()
    (directory / "known-events.json").write_text(
        json.dumps({"events": [{"id": "known-x"}]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="invalid known event"):
        load_known_events(directory)
