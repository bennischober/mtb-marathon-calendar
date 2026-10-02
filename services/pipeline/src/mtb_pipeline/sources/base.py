"""Source adapter framework.

An adapter is responsible for three things:

1. ``fetch`` retrieves raw documents through the shared polite fetcher.
2. ``parse`` turns those documents into validated :class:`Edition` records.
3. ``profile`` declares what the source is, who runs it, and how trustworthy
   it is, so the registry and the health report work without hard-coded lists.

Keeping fetch and parse split means adapters can be unit-tested against
recorded responses without any network access, and a parsing change can be
replayed against the exact bytes stored in ``raw/``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..http_client import FetchedDocument, Fetcher
from ..models import Edition

# Lower rank wins when two sources disagree about the same field.
SourcePriority = Literal[
    "official_organizer",
    "official_federation",
    "event_platform",
    "specialist_aggregator",
    "web_discovery",
]

PRIORITY_RANK: dict[str, int] = {
    "official_organizer": 0,
    "official_federation": 1,
    "event_platform": 2,
    "specialist_aggregator": 3,
    "web_discovery": 4,
}

DataFormat = Literal["json", "xml", "rss", "ics", "html", "pdf"]

# "verified" means the endpoint, robots policy and response shape were checked
# by hand against the live source. "unverified" means an adapter exists but the
# source has not been re-checked since it was written.
ParserStatus = Literal["verified", "unverified", "broken"]

# Whether the source's terms permit or at least do not forbid automated reading.
TermsStatus = Literal["confirmed", "unconfirmed", "forbidden"]


@dataclass(frozen=True)
class CollectOptions:
    limit: int | None = None
    max_documents: int | None = None
    country: str | None = None


@dataclass(frozen=True)
class SourceProfile:
    """Static, human-maintained facts about a source.

    This is deliberately code rather than database rows: it is reviewed in pull
    requests, versioned with the adapter that depends on it, and cannot be
    silently rewritten by a collection run. Runtime state such as last success
    belongs in the ``source_runs`` table instead.
    """

    #: stable identifier stored in provenance records
    name: str
    #: human-facing homepage, documented in docs/DATA_SOURCES.md
    homepage: str
    #: endpoint actually read, when it differs from the homepage
    endpoint: str | None = None
    priority: SourcePriority = "specialist_aggregator"
    #: scope countries this source is expected to contribute
    countries: tuple[str, ...] = ()
    #: how often this source should be collected, in hours
    cadence_hours: int = 24
    data_format: DataFormat = "json"
    parser_status: ParserStatus = "unverified"
    #: robots.txt outcome as observed at verification time
    robots_status: str = "unverified"
    terms_status: TermsStatus = "unconfirmed"
    #: what this source is good and bad at, shown in the status report
    notes: str = ""

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK[self.priority]


class SourceAdapter(ABC):
    """Base class for every calendar source."""

    #: static registry facts, see :class:`SourceProfile`
    profile: SourceProfile

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def homepage(self) -> str:
        return self.profile.homepage

    @abstractmethod
    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        """Retrieve every document this source needs for one collection run."""

    @abstractmethod
    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        """Turn retrieved documents into validated event editions."""
