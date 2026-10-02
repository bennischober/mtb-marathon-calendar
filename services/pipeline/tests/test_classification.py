import pytest

from mtb_pipeline.classification import LONG_DISTANCE_KM, EventSignals, classify


def result(**kwargs):
    return classify(EventSignals(**kwargs))


@pytest.mark.parametrize(
    "discipline",
    [
        "cyclocross",
        "CX",
        "crosscountry",
        "downhill",
        "enduro",
        "hillclimb",
        "ctf",
        "brevet",
        "duathlon",
        "duathlon_cross",
        "triathlon",
        "bikepacking",
        "jedermann",
        "volksradfahren",
        "rennen_24h",
    ],
)
def test_out_of_scope_disciplines_are_excluded(discipline):
    outcome = result(discipline=discipline, category="MTB", max_distance_km=200)

    assert outcome.classification == "exclude"
    assert outcome.confidence >= 0.9


@pytest.mark.parametrize("category", ["ROAD", "gravel", "RTF", "cyclocross"])
def test_non_mtb_categories_are_excluded(category):
    outcome = result(discipline="MTB_MARATHON", category=category, max_distance_km=200)

    assert outcome.classification == "exclude"


def test_gravel_event_with_secondary_mtb_is_excluded():
    outcome = result(discipline="ULTRA", category="GRAVEL", max_distance_km=800)

    assert outcome.classification == "exclude"
    assert "category_gravel_is_not_mtb" in outcome.warnings


def test_marathon_discipline_is_included():
    outcome = result(discipline="MTB_MARATHON", category="MTB", max_distance_km=120)

    assert outcome.classification == "mtb_marathon"
    assert outcome.confidence == 0.9
    assert outcome.warnings == []


def test_marathon_without_long_course_is_kept_with_warning():
    """The 50 km rule is a heuristic, never a hard filter."""
    outcome = result(
        discipline="MTB_MARATHON", category="MTB", max_distance_km=LONG_DISTANCE_KM - 10
    )

    assert outcome.classification == "mtb_marathon"
    assert f"longest_course_variant_below_{LONG_DISTANCE_KM:g}km" in outcome.warnings


def test_ultra_beyond_marathon_distance_is_included():
    outcome = result(discipline="ULTRA", category="MTB", max_distance_km=250)

    assert outcome.classification == "mtb_marathon"
    assert outcome.confidence == 0.75


def test_ultra_without_evidence_becomes_a_candidate():
    outcome = result(discipline="ULTRA", category="MTB")

    assert outcome.classification == "mtb_candidate"
    assert "ultra_without_long_distance_or_multi_day_evidence" in outcome.warnings


def test_multi_day_ultra_is_a_stage_race():
    outcome = result(discipline="ULTRA", category="MTB", max_distance_km=60, duration_days=3)

    assert outcome.classification == "mtb_stage_race"


def test_stage_discipline_requires_multiple_days_for_full_confidence():
    single = result(discipline="ETAPPENTOUR", category="MTB", max_distance_km=300)
    multi = result(discipline="ETAPPENTOUR", category="MTB", max_distance_km=300, duration_days=4)

    assert single.classification == "mtb_stage_race"
    assert single.confidence == 0.7
    assert multi.confidence == 0.9


def test_radmarathon_label_promotes_but_stays_flagged():
    """An ambiguous label may be promoted, but never silently."""
    outcome = result(discipline="RADMARATHON", category="MTB", max_distance_km=200)

    assert outcome.classification == "mtb_marathon"
    assert outcome.confidence == 0.7
    assert "discipline_radmarathon_is_ambiguous" in outcome.warnings


def test_radmarathon_without_distance_goes_to_review():
    outcome = result(discipline="RADMARATHON", category="MTB", max_distance_km=30)

    assert outcome.classification == "mtb_candidate"
    assert "ambiguous_discipline_needs_review" in outcome.warnings


def test_long_distance_event_without_label_is_a_candidate():
    outcome = result(discipline=None, category="MTB", max_distance_km=120)

    assert outcome.classification == "mtb_candidate"
    assert "unlabelled_long_distance_event" in outcome.warnings


def test_short_event_without_label_is_excluded():
    outcome = result(discipline=None, category="MTB", max_distance_km=25)

    assert outcome.classification == "exclude"
    assert "no_long_distance_evidence" in outcome.warnings


def test_missing_distance_data_is_a_candidate():
    outcome = result(discipline=None, category="MTB", max_distance_km=None)

    assert outcome.classification == "mtb_candidate"
    assert "no_distance_data" in outcome.warnings


def test_cancelled_event_keeps_its_classification_and_is_flagged():
    outcome = result(
        discipline="MTB_MARATHON", category="MTB", max_distance_km=100, is_cancelled=True
    )

    assert outcome.classification == "mtb_marathon"
    assert "event_is_cancelled" in outcome.warnings


def test_stage_like_flag_alone_is_not_enough():
    outcome = result(discipline=None, category="MTB", max_distance_km=80, is_stage_like=True)

    assert outcome.classification == "mtb_candidate"


def test_technique_course_is_excluded_even_under_an_ambiguous_mtb_label():
    result = classify(
        EventSignals(
            discipline="Sonstige MTB",
            category="MTB",
            title='MTB-Fahrtechnikkurs "Allround", Nürnberg #5',
        )
    )

    assert result.classification == "exclude"
    assert any(
        warning.startswith("title_suggests_training_not_a_race") for warning in result.warnings
    )


@pytest.mark.parametrize(
    "title",
    [
        "MTB-Fahrtechnikkurs Allround",
        "Technikkurs Kids",
        "Bike Workshop Trail Skills",
        "Gravel-Fortbildung Seminar",
        "Club training ride",
        "Ehrenfahrt des RV Adelby",
    ],
)
def test_training_and_course_titles_are_excluded(title):
    assert classify(EventSignals(title=title)).classification == "exclude"


def test_an_explicit_marathon_label_beats_a_training_like_title():
    """A contradictory record should follow the discipline label, not the title."""
    result = classify(
        EventSignals(discipline="MTB_MARATHON", max_distance_km=95.0, title="Marathon Workshop")
    )

    assert result.classification == "mtb_marathon"


def test_race_titles_are_untouched_by_the_training_rule():
    for title in ("Salzkammergut Trophy", "Elsass' Bike", "Black Forest ULTRA"):
        assert (
            classify(EventSignals(title=title, max_distance_km=100.0)).classification != "exclude"
        )
