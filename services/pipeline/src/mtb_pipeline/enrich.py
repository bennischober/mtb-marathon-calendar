"""Race enrichment: read the organizer's own pages for the sparse facts.

The calendar's automated records are only as rich as the aggregators that
produce them — usually a name, a date, a town. But organizers publish the
rest themselves: the Ausschreibung page carries the price schedule, the
Strecken page carries course distances and elevation, the home page carries
the event description. This stage fetches those pages (plus the organizer's
announcement/registration links when a source provides them) and runs the
same generic LLM extraction over them, merging what it finds into the
existing edition with provenance kept per field.

The enrichment is additive by design: existing automated facts are only
overwritten when the organizer's own page contradicts them (organizer outranks
every aggregator), and every enriched field is recorded with its source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .http_client import Fetcher
from .llm import LLMClassifier
from .llm_extract import extract_edition, extract_partial
from .models import Edition, SourceRef


@dataclass
class EnrichResult:
    editions: list[Edition] = field(default_factory=list)
    pages_read: int = 0
    descriptions_added: int = 0
    descriptions_replaced: int = 0
    prices_added: int = 0
    courses_added: int = 0

    def summary_lines(self) -> list[str]:
        changed = (
            f", {self.descriptions_replaced} descriptions replaced (page changed)"
            if self.descriptions_replaced
            else ""
        )
        return [
            f"enriched {len(self.editions)} races: {self.pages_read} pages read, "
            f"+{self.descriptions_added} descriptions{changed}, "
            f"+{self.prices_added} prices, "
            f"+{self.courses_added} course variants",
        ]


# Subpage names worth following, by what they typically carry.
_PRICE_PAGE_HINTS = ("ausschreibung", "anmeldung", "gebuehr", "gebühren", "preise", "meldung")
_COURSE_PAGE_HINTS = ("strecken", "strecke", "courses", "parcours", "distanzen")


def _subpage_candidates(edition: Edition) -> list[tuple[str, str]]:
    """(url, reason) pairs worth fetching for an edition.

    Priority: explicit source-provided announcement/registration links, then
    well-known subpage names discovered on the official page itself.
    """
    candidates: list[tuple[str, str]] = []
    for link in ("registration_url",):
        value = getattr(edition, link)
        if value:
            candidates.append((str(value), "registration"))
    return candidates


def _discover_subpages(fetcher: Fetcher, base_url: str) -> list[tuple[str, str]]:
    """Follow the official page's own links to price/course subpages."""
    try:
        home = fetcher.get(base_url)
    except Exception:
        return []
    html = home.content.decode("utf-8", errors="ignore")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="([^"#]+)"', html):
        href = match.group(1)
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != urlparse(base_url).netloc:
            continue
        lowered = href.lower()
        kind = None
        if any(hint in lowered for hint in _PRICE_PAGE_HINTS):
            kind = "prices"
        elif any(hint in lowered for hint in _COURSE_PAGE_HINTS):
            kind = "courses"
        if kind and absolute not in seen:
            seen.add(absolute)
            found.append((absolute, kind))
    return found


def enrich_editions(
    editions: list[Edition],
    fetcher: Fetcher,
    classifier: LLMClassifier,
    *,
    max_pages_per_race: int = 3,
) -> EnrichResult:
    """Read organizer pages and merge descriptions, prices and courses.

    Returns new Edition objects; the input list is not mutated.
    """
    result = EnrichResult()
    enriched: list[Edition] = []
    for edition in editions:
        # A federation-calendar PDF is not an organizer site; reading it via
        # the HTML extractor yields nothing. The gap stays visible instead.
        official = str(edition.official_url)
        if re.search(r"\.(pdf|xml|ics)$", urlparse(official).path, re.I):
            enriched.append(edition)
            continue

        pages: list[tuple[str, object]] = []
        try:
            home = fetcher.get(str(edition.official_url))
            pages.append(("home", home))
            subpages = _discover_subpages(fetcher, str(edition.official_url))
            for url, kind in subpages[: max_pages_per_race - 1]:
                try:
                    pages.append((kind, fetcher.get(url)))
                except Exception:
                    continue
        except Exception:
            enriched.append(edition)
            continue

        result.pages_read += len(pages)
        current = edition
        for kind, document in pages:
            # Fast path: this page's content hash is already the newest ref
            # for that URL — the organizer changed nothing, so the stored
            # facts are current. Skip the LLM entirely.
            newest_ref = max(
                (
                    ref
                    for ref in edition.source_refs
                    if ref.url == document.url and ref.content_hash
                ),
                key=lambda ref: ref.retrieved_at,
                default=None,
            )
            if newest_ref is not None and newest_ref.content_hash == document.content_hash:
                continue

            if kind == "home":
                extracted = extract_edition(
                    classifier,
                    source_name="organizer",
                    url=document.url,
                    content=document.content,
                    retrieved_at=document.retrieved_at,
                    content_hash=document.content_hash,
                    confidence=0.75,
                )
                if extracted is not None:
                    current = _merge(extracted, current, kind, result)
                continue

            # Subpages carry partial facts (a course list has no event name).
            from .llm_extract import page_text

            partial = extract_partial(classifier, page_text(document.content))
            if partial is None:
                continue
            current = _merge_partial(partial, current, document, result)
        enriched.append(current)
    result.editions = enriched
    return result


def _merge_partial(partial, edition: Edition, document, result: EnrichResult) -> Edition:
    """Merge a subpage's partial facts; provenance points at the subpage."""

    updates: dict[str, object] = {}
    if partial.description:
        # Replace the description only when the organizer actually changed
        # their page since we last read it: same content hash = keep ours
        # (stable), different hash = the page changed, take the new text.
        previous = max(
            (
                ref
                for ref in edition.source_refs
                if ref.url == document.url and ref.content_hash
            ),
            key=lambda ref: ref.retrieved_at,
            default=None,
        )
        page_changed = (
            previous is None or previous.content_hash != document.content_hash
        )
        if page_changed or not edition.description:
            if edition.description and page_changed:
                result.descriptions_replaced += 1
            else:
                result.descriptions_added += 1
            updates["description"] = partial.description

    if partial.courses:
        source_ref = SourceRef(
            id=f"organizer:subpage:{document.content_hash[:16]}",
            url=document.url,
            retrieved_at=document.retrieved_at,
            supports=["course_variants"],
            confidence=0.75,
            content_hash=document.content_hash,
            adapter="organizer",
        )
        # Match by distance (±5%), not by id: the aggregator names a course
        # "50 km" while the organizer calls it "50km Strecke" — same course,
        # different generated ids. The organizer's facts enrich the matching
        # variant (elevation especially); genuinely new courses are appended.

        updated_variants = list(edition.course_variants)
        added = 0
        for course in partial.courses:
            if not course.distance_km:
                continue
            match = next(
                (
                    variant
                    for variant in updated_variants
                    if abs(variant.distance_km - course.distance_km)
                    <= 0.05 * course.distance_km
                ),
                None,
            )
            if match is not None:
                if course.elevation_m and course.elevation_m != match.elevation_m:
                    updated_variants[updated_variants.index(match)] = match.model_copy(
                        update={"elevation_m": course.elevation_m}
                    )
                continue
            from .llm_extract import _course_variant

            updated_variants.append(
                _course_variant(edition.event_id, edition.edition, course, source_ref.id)
            )
            added += 1
        if added or updated_variants != edition.course_variants:
            updates["course_variants"] = updated_variants
            result.courses_added += added
            new_refs = list(edition.source_refs)
            if all(source_ref.id != ref.id for ref in new_refs):
                new_refs.append(source_ref)
            updates["source_refs"] = new_refs

    if partial.prices:
        from .models import PriceEntry

        existing = {(price.amount, price.label) for price in edition.prices}
        fresh = [
            PriceEntry(label=price.label, amount=price.amount, note=price.note)
            for price in partial.prices
            if price.amount and (price.amount, price.label) not in existing
        ]
        if fresh:
            updates["prices"] = [*edition.prices, *fresh]
            result.prices_added += len(fresh)
            new_refs = list(updates.get("source_refs", edition.source_refs))
            source_ref = SourceRef(
                id=f"organizer:subpage:{document.content_hash[:16]}",
                url=document.url,
                retrieved_at=document.retrieved_at,
                supports=["prices"],
                confidence=0.75,
                content_hash=document.content_hash,
                adapter="organizer",
            )
            if all(source_ref.id != ref.id for ref in new_refs):
                new_refs.append(source_ref)
            updates["source_refs"] = new_refs

    if not updates:
        return edition
    return edition.model_copy(update=updates)


def _merge(
    extracted: Edition,
    edition: Edition,
    kind: str,
    result: EnrichResult,
) -> Edition:
    """Additively merge an extraction into the edition; organizer facts win."""
    updates: dict[str, object] = {}

    if extracted.description and not edition.description:
        updates["description"] = extracted.description
        result.descriptions_added += 1

    if extracted.prices:
        existing = {(price.amount, price.label) for price in edition.prices}
        fresh = [
            price
            for price in extracted.prices
            if (price.amount, price.label) not in existing
        ]
        if fresh:
            updates["prices"] = [*edition.prices, *fresh]
            result.prices_added += len(fresh)

    if extracted.course_variants:
        # Match by distance (±5%), not by id: the aggregator names a course
        # "50 km" while the organizer's page calls it "50km Strecke" — same
        # route, different generated ids. Organizer facts enrich the match.
        updated_variants = list(edition.course_variants)
        added = 0
        for course in extracted.course_variants:
            match = next(
                (
                    variant
                    for variant in updated_variants
                    if abs(variant.distance_km - course.distance_km)
                    <= 0.05 * course.distance_km
                ),
                None,
            )
            if match is not None:
                if course.elevation_m and course.elevation_m != match.elevation_m:
                    updated_variants[updated_variants.index(match)] = match.model_copy(
                        update={"elevation_m": course.elevation_m}
                    )
                continue
            updated_variants.append(course)
            added += 1
        if added or updated_variants != edition.course_variants:
            updates["course_variants"] = updated_variants
            result.courses_added += added

    # The organizer's own page outranks aggregators on facts.
    if extracted.date_start != edition.date_start:
        updates["date_start"] = extracted.date_start
    if extracted.date_end and extracted.date_end != edition.date_end:
        updates["date_end"] = extracted.date_end

    # Provenance: attach the extraction's source_ref (the organizer page).
    # One ref per page URL: a re-read replaces the previous ref for the same
    # URL (its hash documents what was read when).
    new_refs = [ref for ref in edition.source_refs if ref.url not in {r.url for r in extracted.source_refs}]
    new_refs.extend(extracted.source_refs)
    updates["source_refs"] = new_refs

    if not updates:
        return edition
    return edition.model_copy(update=updates)
