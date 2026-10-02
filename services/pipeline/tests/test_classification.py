"""Deterministic scope filter + LLM hand-off contract.

The rules only act on source-stated labels (facts); semantic judgement is
the LLM's job. These tests pin the filter behavior and the guarantees the
LLM stage can rely on.
"""

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
        "e-bike",
        "pumptrack",
        "einzelzeitfahren",
    ],
)
def test_out_of_scope_disciplines_are_excluded(discipline):
    """Source-stated gravity/road/other-sport disciplines are facts."""
    outcome = result(discipline=discipline, category="MTB", max_distance_km=200)

    assert outcome.classification == "exclude"
    assert outcome.confidence >= 0.9


@pytest.mark.parametrize(
    "category", ["ROAD", "gravel", "RTF", "cyclocross", "brevet", "volksradfahren"]
)
def test_non_mtb_categories_are_excluded(category):
    outcome = result(discipline="MTB_MARATHON", category=category, max_distance_km=200)

    assert outcome.classification == "exclude"


def test_gravel_event_with_secondary_mtb_is_excluded():
    outcome = result(discipline="ULTRA", category="GRAVEL", max_distance_km=800)

    assert outcome.classification == "exclude"
    assert "category_gravel_is_not_mtb" in outcome.warnings


def test_a_source_mtb_marathon_label_does_not_decide_the_final_class():
    """Semantic judgement is the LLM's job: labelled marathons are candidates."""
    outcome = result(discipline="MTB_MARATHON", category="MTB", max_distance_km=120)

    assert outcome.classification == "mtb_candidate"
    assert outcome.warnings == []


def test_distance_facts_become_warnings_not_decisions():
    outcome = result(
        discipline="MTB_MARATHON", category="MTB", max_distance_km=LONG_DISTANCE_KM - 10
    )

    assert outcome.classification == "mtb_candidate"
    assert f"longest_course_variant_below_{LONG_DISTANCE_KM:g}km" in outcome.warnings


def test_no_distance_data_is_recorded_but_does_not_exclude():
    outcome = result(discipline="MTB_MARATHON", category="MTB", max_distance_km=None)

    assert outcome.classification == "mtb_candidate"
    assert "no_distance_data" in outcome.warnings


def test_a_running_race_labelled_mtb_stays_a_candidate():
    """Possenlauf: the source labels it MTB_MARATHON; only the LLM can read
    the description and exclude it. The filter must not guess from the title."""
    outcome = result(
        discipline="MTB_MARATHON",
        category="MTB",
        max_distance_km=14.0,
        title="Possenlauf",
        description=(
            "Das Sportevent richtet sich an Lauf- und Radsportbegeisterte "
            "aller Altersklassen. Neben Laufangeboten gehört auch "
            "Mountainbike zum Programm."
        ),
    )

    assert outcome.classification == "mtb_candidate"


def test_cancelled_is_a_warning_the_llm_can_see():
    outcome = result(
        discipline="MTB_MARATHON", category="MTB", max_distance_km=80, is_cancelled=True
    )

    assert outcome.classification == "mtb_candidate"
    assert "event_is_cancelled" in outcome.warnings


def test_extra_warnings_survive_the_filter():
    outcome = result(
        discipline="MTB_MARATHON",
        category="MTB",
        max_distance_km=80,
        extra_warnings=["federation_calendar_pdf"],
    )

    assert "federation_calendar_pdf" in outcome.warnings


def test_unlabelled_records_stay_candidates():
    """A record with no labels at all is the LLM's to judge."""
    outcome = result(max_distance_km=60.0)

    assert outcome.classification == "mtb_candidate"
