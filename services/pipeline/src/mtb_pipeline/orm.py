from datetime import date
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


edition_sources = Table(
    "edition_sources",
    Base.metadata,
    Column("edition_id", ForeignKey("editions.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
)


course_variant_sources = Table(
    "course_variant_sources",
    Base.metadata,
    Column(
        "course_variant_id",
        ForeignKey("course_variants.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    editions: Mapped[list["EditionRecord"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )


class EditionRecord(Base):
    __tablename__ = "editions"
    __table_args__ = (UniqueConstraint("event_id", "edition", name="uq_edition_event_year"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False)
    edition: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date | None] = mapped_column(Date)
    format: Mapped[str] = mapped_column(String, nullable=False)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    previous_edition_id: Mapped[str | None] = mapped_column(String)
    official_url: Mapped[str] = mapped_column(String, nullable=False)
    registration_url: Mapped[str | None] = mapped_column(String)
    classification: Mapped[str] = mapped_column(String, nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    editorial_profile: Mapped[dict | None] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="unreviewed")
    review_reason: Mapped[str | None] = mapped_column(String)
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_verified: Mapped[date | None] = mapped_column(Date)

    event: Mapped[EventRecord] = relationship(back_populates="editions")
    course_variants: Mapped[list["CourseVariantRecord"]] = relationship(
        back_populates="edition",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CourseVariantRecord.position",
    )
    source_links: Mapped[list["SourceRecord"]] = relationship(
        secondary=edition_sources,
        lazy="selectin",
    )


class CourseVariantRecord(Base):
    __tablename__ = "course_variants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    edition_id: Mapped[str] = mapped_column(ForeignKey("editions.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    start_location: Mapped[str | None] = mapped_column(String)
    finish_location: Mapped[str | None] = mapped_column(String)
    technical_notes: Mapped[str | None] = mapped_column(String)
    availability: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    edition: Mapped[EditionRecord] = relationship(back_populates="course_variants")
    source_links: Mapped[list["SourceRecord"]] = relationship(
        secondary=course_variant_sources,
        lazy="selectin",
    )


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    retrieved_at: Mapped[str] = mapped_column(String, nullable=False)
    supports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String)
    native_id: Mapped[str | None] = mapped_column(String)
    adapter: Mapped[str | None] = mapped_column(String)


class MergeConflictRecord(Base):
    """One field where two sources disagreed while merging duplicate editions.

    The merge itself already resolves every conflict, so this table is not a
    queue of things left broken. It is a record of what disagreed and which
    value survived, so a surprising merge can be traced back to the sources
    that caused it instead of being rediscovered from the export log.

    Rows describe the most recent export: they are replaced, not accumulated, so
    a field that stopped disagreeing stops being reported.
    """

    __tablename__ = "merge_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edition_id: Mapped[str] = mapped_column(
        ForeignKey("editions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[str] = mapped_column(String, nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False)
    kept: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    kept_source: Mapped[str] = mapped_column(String, nullable=False)
    discarded: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String, nullable=False)


class CollectionRunRecord(Base):
    """One attempt to collect a single source.

    Runs are append-only on purpose. Source health is derived from the whole
    history rather than a mutable "last status" column, so a transient failure
    cannot erase the record of a source that worked yesterday.
    """

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[str] = mapped_column(String, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(String)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kept: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
