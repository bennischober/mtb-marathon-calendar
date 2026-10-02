"""Source adapter framework.

An adapter is responsible for two things and nothing else:

1. ``fetch`` retrieves raw documents through the shared polite fetcher.
2. ``parse`` turns those documents into validated :class:`Edition` records.

Keeping the split means adapters can be unit-tested against recorded
responses without any network access, and a parsing change can be replayed
against the exact bytes stored in ``raw/``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from ..http_client import FetchedDocument, Fetcher
from ..models import Edition


@dataclass(frozen=True)
class CollectOptions:
    limit: int | None = None
    max_documents: int | None = None
    country: str | None = None


class SourceAdapter(ABC):
    """Base class for every calendar source."""

    #: stable identifier stored in provenance records
    name: str = "unknown"

    #: human-facing homepage, documented in docs/DATA_SOURCES.md
    homepage: str = ""

    @abstractmethod
    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        """Retrieve every document this source needs for one collection run."""

    @abstractmethod
    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        """Turn retrieved documents into validated event editions."""
