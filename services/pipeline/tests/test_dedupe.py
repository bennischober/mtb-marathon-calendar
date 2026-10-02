"""Deduplication and conflict resolution.

The risk being guarded against here is symmetric: folding two unrelated races
into one record, and splitting one race into two. Both are silent failures that
only surface later as a wrong calendar.
"""

from mtb_pipeline.dedupe import (
    MergeConflict,
    merge_editions,
)
from mtb_pipeline.sources.base import PRIORITY_RANK

PRIORITY = PRIORITY_RANK


def ref(
    adapter: str, *, ref_id: str | None = None, retrieved_at: str = "2026-10-01T00:00:00+00:00"
):
    return {
        "id": ref_id or f"{adapter}-ref",
        "adapter": adapter,
        "url": f"https://{adapter}.example/event",
        "retrieved_at": retrieved_at,
        "confidence": 0.9,
    }


def edition(
    *,
    id: str = "a",
    name: str = "Salzkammergut Trophy",
    date_start: str = "2027-07-17",
    date_end: str | None = None,
    country: str = "AT",
    year: int = 2027,
    official_url: str = "https://organizer.example",
    registration_url: str | None = None,
    source_refs=None,
    course_variants=None,
    location=None,
):
    payload = {
        "id": id,
        "event_id": "salzkammergut",
        "edition": year,
        "name": name,
        "date_start": date_start,
        "format": "single_day",
        "location": {"name": "St. Wolfgang", "country": country} if location is None else location,
        "official_url": official_url,
        "classification": "mtb_marathon",
        "status": "registration_open",
        "source_refs": source_refs if source_refs is not None else [ref("organizer")],
    }
    if date_end:
        payload["date_end"] = date_end
    if registration_url:
        payload["registration_url"] = registration_url
    if course_variants:
        payload["course_variants"] = course_variants
    return payload


def test_a_single_record_passes_through_untouched():
    record = edition()

    result = merge_editions([record])

    assert result.editions == [record]
    assert result.merges == 0
    assert result.conflicts == []


def test_the_same_race_from_two_sources_is_merged():
    records = [
        edition(
            id="from-aggregator",
            name="Salzkammergut Trophy",
            official_url="https://aggregator.example/x",
            source_refs=[ref("specialist_aggregator")],
        ),
        edition(
            id="from-organizer",
            name="Salzkammergut MTB Marathon",
            official_url="https://organizer.example",
            source_refs=[ref("official_organizer")],
        ),
    ]

    result = merge_editions(records)

    assert result.merges == 1
    assert len(result.editions) == 1
    assert result.editions[0]["official_url"] == "https://organizer.example"


def test_every_source_reference_survives_a_merge():
    records = [
        edition(id="a", source_refs=[ref("specialist_aggregator")]),
        edition(id="b", name="Salzkammergut Trophy 2027", source_refs=[ref("official_organizer")]),
    ]

    result = merge_editions(records)

    adapters = {r["adapter"] for r in result.editions[0]["source_refs"]}
    assert adapters == {"specialist_aggregator", "official_organizer"}


def test_a_generic_name_is_never_enough_to_merge():
    """Two different "Bike Marathon" events must not be folded together."""
    records = [
        edition(id="a", name="Bike Marathon", country="DE"),
        edition(id="b", name="Bike Marathon", country="FR"),
    ]

    result = merge_editions(records)

    assert result.merges == 0
    assert len(result.editions) == 2


def test_different_countries_are_never_merged():
    records = [
        edition(id="a", name="Black Forest Ultra", country="DE"),
        edition(id="b", name="Black Forest Ultra", country="CH"),
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2


def test_different_years_are_never_merged():
    records = [
        edition(id="a", name="Swiss Epic", year=2027, date_start="2027-06-12"),
        edition(id="b", name="Swiss Epic", year=2028, date_start="2028-06-11"),
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2


def test_a_reschedule_beyond_tolerance_is_kept_as_two_records():
    """Better two honest records than one that silently picks a date."""
    records = [
        edition(id="a", date_start="2027-07-17"),
        edition(id="b", date_start="2027-09-05"),
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2
    assert any("dates differ" in line for line in result.log)
    assert any(c["field"] == "date_start" for c in result.conflicts)


def test_a_small_date_difference_is_treated_as_the_same_event():
    records = [
        edition(id="a", date_start="2027-07-17"),
        edition(id="b", date_start="2027-07-18"),
    ]

    result = merge_editions(records)

    assert result.merges == 1
    assert len(result.editions) == 1


def test_a_conflicting_date_is_recorded_rather_than_dropped():
    records = [
        edition(id="a", date_start="2027-07-17", source_refs=[ref("official_organizer")]),
        edition(id="b", date_start="2027-09-05", source_refs=[ref("specialist_aggregator")]),
    ]

    result = merge_editions(records)
    conflict = next(c for c in result.conflicts if c["field"] == "date_start")

    assert isinstance(conflict, MergeConflict)
    assert conflict["discarded"]
    assert conflict["reason"]


def test_a_lower_priority_source_cannot_overwrite_a_higher_priority_value():
    records = [
        edition(
            id="from-organizer",
            official_url="https://organizer.example",
            source_refs=[ref("official_organizer")],
        ),
        edition(
            id="from-aggregator",
            official_url="https://aggregator.example/broken",
            source_refs=[ref("specialist_aggregator")],
        ),
    ]

    result = merge_editions(records)

    assert result.editions[0]["official_url"] == "https://organizer.example"


def test_a_higher_priority_value_wins_and_the_disagreement_is_kept():
    records = [
        edition(
            id="from-aggregator",
            registration_url="https://aggregator.example/register",
            source_refs=[ref("specialist_aggregator")],
        ),
        edition(
            id="from-organizer",
            registration_url="https://organizer.example/register",
            source_refs=[ref("official_organizer")],
        ),
    ]

    result = merge_editions(records)
    conflict = next(c for c in result.conflicts if c["field"] == "registration_url")

    assert result.editions[0]["registration_url"] == "https://organizer.example/register"
    assert conflict["kept"] == "https://organizer.example/register"
    assert conflict["discarded"]["specialist_aggregator"] == "https://aggregator.example/register"


def test_fresher_data_breaks_a_priority_tie():
    records = [
        edition(
            id="old",
            official_url="https://old.example",
            source_refs=[ref("event_platform", retrieved_at="2026-01-01T00:00:00+00:00")],
        ),
        edition(
            id="new",
            official_url="https://new.example",
            source_refs=[ref("event_platform", retrieved_at="2026-10-01T00:00:00+00:00")],
        ),
    ]

    result = merge_editions(records)

    assert result.editions[0]["official_url"] == "https://new.example"


def test_course_variants_are_unioned_without_duplicate_ids():
    records = [
        edition(
            id="a",
            source_refs=[ref("official_organizer")],
            course_variants=[{"id": "v1", "distance_km": 90}, {"id": "v2", "distance_km": 50}],
        ),
        edition(
            id="b",
            name="Salzkammergut Trophy 2027",
            source_refs=[ref("specialist_aggregator")],
            course_variants=[
                {"id": "v2", "distance_km": 50},
                {"id": "v3", "distance_km": 210},
            ],
        ),
    ]

    result = merge_editions(records)
    ids = [variant["id"] for variant in result.editions[0]["course_variants"]]

    assert ids == ["v1", "v2", "v3"]
    assert len(ids) == len(set(ids))


def test_a_location_field_is_filled_in_when_missing():
    records = [
        edition(id="a", location={"name": "St. Wolfgang", "country": "AT"}),
        edition(
            id="b",
            name="Salzkammergut Trophy 2027",
            location={"name": "St. Wolfgang", "country": "AT", "region": "Salzburg"},
        ),
    ]

    result = merge_editions(records)

    assert result.editions[0]["location"]["region"] == "Salzburg"


def test_a_location_disagreement_is_recorded():
    records = [
        edition(
            id="a",
            location={"name": "St. Wolfgang", "country": "AT", "region": "Salzburg"},
            source_refs=[ref("official_organizer")],
        ),
        edition(
            id="b",
            name="Salzkammergut Trophy 2027",
            location={"name": "Bad Ischl", "country": "AT", "region": "Upper Austria"},
            source_refs=[ref("specialist_aggregator")],
        ),
    ]

    result = merge_editions(records)
    conflict = next(c for c in result.conflicts if c["field"].startswith("location."))

    assert result.editions[0]["location"]["name"] == "St. Wolfgang"
    assert conflict["discarded"]["specialist_aggregator"] == "Bad Ischl"


def test_a_year_suffix_does_not_split_one_race_into_two():
    records = [
        edition(id="a", name="Elsäss Bike 2027"),
        edition(id="b", name="Elsäss Bike"),
    ]

    result = merge_editions(records)

    assert result.merges == 1


def test_a_differently_spelled_name_stays_separate_rather_than_guessing():
    """French "ss" and German "ss" are not normalised together.

    Guessing here would risk folding two real races together, so the records are
    kept apart and a human can join them in data/curated/ instead.
    """
    records = [
        edition(id="a", name="Elsass' Bike"),
        edition(id="b", name="Elsäss Bike"),
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2
    assert result.merges == 0


def test_unrelated_races_are_left_alone():
    records = [
        edition(id="a", name="Salzkammergut Trophy"),
        edition(id="b", name="Black Forest Ultra", date_start="2027-07-25"),
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2
    assert result.merges == 0


def test_records_without_dates_are_never_merged():
    records = [
        edition(id="a", date_start="2027-07-17"),
        {**edition(id="b"), "date_start": None},
    ]

    result = merge_editions(records)

    assert len(result.editions) == 2


def test_merging_three_sources_reports_two_merges():
    records = [
        edition(id="a", source_refs=[ref("official_organizer")]),
        edition(
            id="b",
            name="Salzkammergut MTB Marathon 2027",
            source_refs=[ref("specialist_aggregator")],
        ),
        edition(id="c", name="Salzkammergut Trophy AT", source_refs=[ref("event_platform")]),
    ]

    result = merge_editions(records)

    assert result.merges == 2
    assert len(result.editions) == 1
    assert len(result.editions[0]["source_refs"]) == 3


def test_no_input_record_is_ever_dropped():
    """A regression guard: non-matching records must survive, not vanish."""
    records = [
        edition(id="keep-1", name="Salzkammergut Trophy", date_start="2027-07-17"),
        edition(id="keep-2", name="Black Forest Ultra", date_start="2027-07-25"),
        edition(id="keep-3", name="Wasgau Bike Marathon", date_start="2027-10-10"),
        edition(id="resched-1", name="Eiger Bike Challenge", date_start="2027-08-14"),
        edition(id="resched-2", name="Eiger Bike Challenge", date_start="2027-10-02"),
    ]

    result = merge_editions(records)

    assert {item["id"] for item in result.editions} == {
        "keep-1",
        "keep-2",
        "keep-3",
        "resched-1",
        "resched-2",
    }


def test_merging_a_record_into_another_keeps_the_survivor_count_right():
    records = [
        edition(id="a", source_refs=[ref("official_organizer")]),
        edition(
            id="b",
            name="Salzkammergut MTB Marathon 2027",
            source_refs=[ref("specialist_aggregator")],
        ),
        edition(id="c", name="Black Forest Ultra", date_start="2027-07-25"),
    ]

    result = merge_editions(records)

    assert result.merges == 1
    assert len(result.editions) == 2
