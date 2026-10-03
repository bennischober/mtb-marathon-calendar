"""Discovery pipeline: prefilter, LLM screening and newness detection."""

from mtb_pipeline.discovery import (
    DiscoveryCandidate,
    _known,
    evaluate_candidates,
    prefilter_country,
)


def _candidate(slug: str, name: str, description: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        source="stub", slug=slug, url=f"https://x/{slug}", name=name, description=description
    )


def test_prefilter_keeps_country_signals():
    in_scope = _candidate("a", "Alpentour Trophy", "MTB stage race in the Austrian Alps")
    out = _candidate("b", "Andes Ultra", "A race through the Andes")

    survivors = prefilter_country([in_scope, out])

    assert [c.slug for c in survivors] == ["a"]


def test_known_matches_despite_sponsor_prefix():
    known = {"Swiss Epic", "BIKE Festival Riva del Garda", "The HOPE 1000"}

    assert _known("SPAR Swiss Epic", known)
    assert _known("Swiss Epic", known)
    assert _known("Hope 1000", known)
    assert not _known("Appenninica MTB Stage Race", known)


def test_generic_words_never_cause_a_match():
    assert not _known("Some MTB Race", {"Another MTB Race"})


class StubClassifier:
    model_name = "stub"

    def __init__(self, verdicts: dict[str, dict]) -> None:
        self.verdicts = verdicts
        self.asked: list[str] = []

    def discovery_single(self, prompt: str) -> dict | None:
        self.asked.append(prompt)
        for needle, verdict in self.verdicts.items():
            if needle.lower() in prompt.lower():
                return verdict
        return None


def test_evaluate_marks_new_in_scope_candidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    candidates = [
        _candidate("new", "Appenninica MTB Stage Race", "Italian stage race"),
        _candidate("known", "SPAR Swiss Epic", "Swiss stage race"),
    ]
    stub = StubClassifier(
        {
            "appenninica": {
                "verdict": "in_scope",
                "country": "IT",
                "reason": "stage race",
            },
            "spar swiss epic": {
                "verdict": "in_scope",
                "country": "CH",
                "reason": "stage race",
            },
        }
    )

    evaluated = evaluate_candidates(candidates, stub, known_names={"Swiss Epic"})  # type: ignore[arg-type]

    by_slug = {c.slug: c for c in evaluated}
    assert by_slug["new"].verdict == "in_scope"
    assert "[new]" in by_slug["new"].reason
    assert "[new]" not in by_slug["known"].reason


def test_an_unverdicted_candidate_is_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    candidates = [_candidate("x", "Mystery Event", "No idea")]
    stub = StubClassifier({})

    evaluated = evaluate_candidates(candidates, stub, known_names=set())  # type: ignore[arg-type]

    assert evaluated == []
