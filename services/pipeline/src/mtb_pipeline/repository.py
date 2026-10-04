from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CourseVariant,
    Edition,
    EditorialProfile,
    Location,
    PriceEntry,
    Review,
    SourceRef,
)
from .orm import CourseVariantRecord, EditionRecord, EventRecord, SourceRecord


def _dump_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _load_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def upsert_editions(session: Session, editions: list[Edition]) -> None:
    for edition in editions:
        event_record = session.get(EventRecord, edition.event_id)
        if event_record is None:
            event_record = EventRecord(id=edition.event_id, name=edition.name)
            session.add(event_record)
        else:
            event_record.name = edition.name

        edition_record = session.get(EditionRecord, edition.id)
        human_review: tuple[str, str | None, str | None, str | None] | None = None
        human_editorial: dict | None = None
        if edition_record is None:
            edition_record = EditionRecord(id=edition.id, event=event_record)
            session.add(edition_record)
        else:
            edition_record.event = event_record
            # Human data outranks aggregation, forever: a review decision or an
            # editorial profile written by a person must survive every
            # re-collect, because the incoming aggregated record carries only
            # machine defaults ("unreviewed", no profile). Preserve whatever
            # the database holds and re-apply it below.
            if edition_record.review_status != "unreviewed":
                human_review = (
                    edition_record.review_status,
                    edition_record.review_reason,
                    edition_record.reviewed_by,
                    edition_record.reviewed_at,
                )
            if edition_record.editorial_profile is not None:
                human_editorial = edition_record.editorial_profile

        edition_record.event_id = edition.event_id
        edition_record.edition = edition.edition
        edition_record.name = edition.name
        edition_record.description = edition.description
        edition_record.date_start = edition.date_start
        edition_record.date_end = edition.date_end
        edition_record.start_time = edition.start_time
        edition_record.format = edition.format
        edition_record.duration_days = edition.duration_days
        edition_record.location_name = edition.location.name
        edition_record.country = edition.location.country
        edition_record.region = edition.location.region
        edition_record.lat = edition.location.lat
        edition_record.lng = edition.location.lng
        edition_record.previous_edition_id = edition.previous_edition_id
        edition_record.official_url = str(edition.official_url)
        edition_record.registration_url = (
            str(edition.registration_url) if edition.registration_url else None
        )
        edition_record.prices = [
            price.model_dump(mode="json", exclude_none=True) for price in edition.prices
        ]
        edition_record.classification = edition.classification
        edition_record.classification_confidence = edition.classification_confidence
        edition_record.extraction_warnings = list(edition.extraction_warnings)
        edition_record.editorial_profile = (
            edition.editorial_profile.model_dump(mode="json", exclude_none=True)
            if edition.editorial_profile
            else human_editorial
        )
        if human_review is not None:
            # Keep the human decision; aggregation cannot review.
            edition_record.review_status = human_review[0]
            edition_record.review_reason = human_review[1]
            edition_record.reviewed_by = human_review[2]
            edition_record.reviewed_at = human_review[3]
        else:
            edition_record.review_status = edition.review.status
            edition_record.review_reason = edition.review.reason
            edition_record.reviewed_by = edition.review.reviewed_by
            edition_record.reviewed_at = _dump_timestamp(edition.review.reviewed_at)
        edition_record.status = edition.status
        edition_record.last_verified = edition.last_verified

        source_records: dict[str, SourceRecord] = {}
        for source in edition.source_refs:
            source_record = session.get(SourceRecord, source.id)
            if source_record is None:
                source_record = SourceRecord(id=source.id)
                session.add(source_record)
            source_record.url = str(source.url)
            source_record.retrieved_at = _dump_timestamp(source.retrieved_at)
            source_record.supports = list(source.supports)
            source_record.confidence = source.confidence
            source_record.content_hash = source.content_hash
            source_record.native_id = source.native_id
            source_record.adapter = source.adapter
            source_records[source.id] = source_record

        session.flush()
        edition_record.source_links = list(source_records.values())

        edition_record.course_variants.clear()
        for position, variant in enumerate(edition.course_variants):
            variant_record = CourseVariantRecord(
                id=variant.id,
                edition=edition_record,
                position=position,
                name=variant.name,
                distance_km=variant.distance_km,
                elevation_m=variant.elevation_m,
                start_location=variant.start_location,
                finish_location=variant.finish_location,
                technical_notes=variant.technical_notes,
                availability=variant.availability,
                status=variant.status,
            )
            variant_record.source_links = [
                source_records[source_id]
                for source_id in variant.source_refs
                if source_id in source_records
            ]
            edition_record.course_variants.append(variant_record)

        session.flush()


def load_editions(session: Session) -> list[Edition]:
    records = session.scalars(
        select(EditionRecord).order_by(EditionRecord.date_start, EditionRecord.id)
    ).all()
    return [_to_model(record) for record in records]


def _to_model(record: EditionRecord) -> Edition:
    sources = [
        SourceRef(
            id=source.id,
            url=source.url,
            retrieved_at=_load_timestamp(source.retrieved_at),
            supports=source.supports or [],
            confidence=source.confidence,
            content_hash=source.content_hash,
            native_id=source.native_id,
            adapter=source.adapter,
        )
        for source in record.source_links
    ]
    courses = [
        CourseVariant(
            id=course.id,
            name=course.name,
            distance_km=course.distance_km,
            elevation_m=course.elevation_m,
            start_location=course.start_location,
            finish_location=course.finish_location,
            technical_notes=course.technical_notes,
            availability=course.availability,
            status=course.status,
            source_refs=[source.id for source in course.source_links],
        )
        for course in record.course_variants
    ]
    return Edition(
        id=record.id,
        event_id=record.event_id,
        edition=record.edition,
        name=record.name,
        description=record.description,
        date_start=record.date_start,
        date_end=record.date_end,
        start_time=record.start_time,
        format=record.format,
        duration_days=record.duration_days,
        location=Location(
            name=record.location_name,
            country=record.country,
            region=record.region,
            lat=record.lat,
            lng=record.lng,
        ),
        course_variants=courses,
        previous_edition_id=record.previous_edition_id,
        official_url=record.official_url,
        registration_url=record.registration_url,
        prices=[PriceEntry.model_validate(price) for price in record.prices or []],
        source_refs=sources,
        classification=record.classification,
        classification_confidence=record.classification_confidence,
        extraction_warnings=list(record.extraction_warnings or []),
        editorial_profile=(
            EditorialProfile.model_validate(record.editorial_profile)
            if record.editorial_profile
            else None
        ),
        review=Review(
            status=record.review_status,
            reason=record.review_reason,
            reviewed_by=record.reviewed_by,
            reviewed_at=_load_timestamp(record.reviewed_at),
        ),
        status=record.status,
        last_verified=record.last_verified,
    )

def prune_stale_editions(session: Session, adapter_name: str, keep_ids: list[str]) -> int:
    """Delete this adapter's editions that the latest run no longer produces.

    A classification change (e.g. the LLM now excludes a race) or a dropped
    source record would otherwise leave the old row in the database forever:
    upsert only updates what it sees. Pruning keeps the database an accurate
    image of what the sources currently say. Editions that also carry another
    adapter's source reference survive — another source still vouches for
    them.
    """
    keep = set(keep_ids)
    stale = [
        record
        for record in session.scalars(select(EditionRecord)).all()
        if record.id not in keep
        and record.source_links
        and all(source.adapter == adapter_name for source in record.source_links)
    ]
    for record in stale:
        session.delete(record)
    return len(stale)
