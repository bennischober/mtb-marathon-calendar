from datetime import date

from sqlalchemy import (
    JSON,
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
