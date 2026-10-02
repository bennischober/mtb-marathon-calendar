from datetime import date, datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

EventFormat = Literal["single_day", "point_to_point", "stage_race", "multi_day"]
Classification = Literal[
    "mtb_marathon",
    "mtb_ultra",
    "mtb_stage_race",
    "mtb_point_to_point",
    "mtb_candidate",
    "exclude",
]
EventStatus = Literal[
    "announced",
    "active",
    "registration_open",
    "sold_out",
    "cancelled",
    "postponed",
    "unknown",
]
CourseStatus = Literal[
    "announced",
    "active",
    "registration_open",
    "sold_out",
    "cancelled",
    "postponed",
    "unknown",
]
ReviewStatus = Literal["unreviewed", "approved", "rejected", "false_positive"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(StrictModel):
    name: str = Field(min_length=1)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    region: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class SourceRef(StrictModel):
    id: str = Field(min_length=1)
    url: AnyHttpUrl
    retrieved_at: datetime
    supports: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    content_hash: str | None = None
    native_id: str | None = None
    adapter: str | None = None


class CourseVariant(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    distance_km: float = Field(gt=0)
    elevation_m: float | None = Field(default=None, ge=0)
    start_location: str | None = None
    finish_location: str | None = None
    technical_notes: str | None = None
    availability: Literal["confirmed", "announced", "unknown", "sold_out", "cancelled"] = "unknown"
    status: CourseStatus = "unknown"
    source_refs: list[str] = Field(default_factory=list)


class Review(StrictModel):
    status: ReviewStatus = "unreviewed"
    reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class EditorialProfile(StrictModel):
    summary: str | None = None
    route_character: str | None = None
    scenery: str | None = None
    organization: str | None = None
    technical_difficulty: str | None = None
    contributor: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    reviewed_at: datetime | None = None


class Edition(StrictModel):
    id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    edition: int = Field(ge=1900, le=2200)
    name: str = Field(min_length=1)
    description: str | None = None
    date_start: date
    date_end: date | None = None
    format: EventFormat
    duration_days: int | None = Field(default=None, ge=1)
    location: Location
    course_variants: list[CourseVariant] = Field(default_factory=list)
    previous_edition_id: str | None = None
    official_url: AnyHttpUrl
    registration_url: AnyHttpUrl | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    classification: Classification
    classification_confidence: float = Field(ge=0, le=1)
    extraction_warnings: list[str] = Field(default_factory=list)
    editorial_profile: EditorialProfile | None = None
    review: Review = Field(default_factory=Review)
    status: EventStatus
    last_verified: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "Edition":
        if self.date_end is not None and self.date_end < self.date_start:
            raise ValueError("date_end must not be before date_start")

        if self.format == "stage_race" and self.date_end is None:
            raise ValueError("stage races require date_end")

        if self.date_end is not None:
            expected_duration = (self.date_end - self.date_start).days + 1
            if self.duration_days is not None and self.duration_days != expected_duration:
                raise ValueError("duration_days must match date_start and date_end")

        return self
