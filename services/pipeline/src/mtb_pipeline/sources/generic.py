"""The generic fetch adapter: a source is just a list of pages.

Every hand-written parser (radsport-events' field mapping, the bawue PDF
discipline tables) encoded site knowledge in code. The LLM extraction stage
replaces that: a source now only needs to know *which pages to fetch* —
that part is mechanical navigation (a sitemap, a calendar with filter
postbacks, a listing page) and stays code because it is not parsing.

``GenericSourceAdapter`` + a page-listing strategy is a complete adapter:

    class FFCSource(GenericSourceAdapter):
        profile = SourceProfile(name="ffc", ...)

        def pages(self, fetcher, options) -> list[FetchedDocument]:
            ...  # navigate the calendar, return event pages

    # fetch → pages; parse → extract_edition per page. That is the whole thing.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence

from ..http_client import FetchedDocument, Fetcher
from ..llm import LLMClassifier
from ..llm_extract import extract_edition
from ..models import Edition
from .base import CollectOptions, SourceAdapter


class GenericSourceAdapter(SourceAdapter):
    """Fetch pages, extract each with the LLM, validate.

    ``pages`` does the source's mechanical navigation. Parsing is one shared
    implementation for every source.
    """

    #: model confidence for LLM-extracted facts of this source
    extraction_confidence: float = 0.85

    def __init__(self, classifier: LLMClassifier) -> None:
        if classifier is None:
            raise ValueError(
                f"{self.profile.name}: the generic source needs an LLM "
                "classifier (configure [llm] in mtb-pipeline.toml)"
            )
        self._classifier = classifier

    @abstractmethod
    def pages(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        """Retrieve the event pages this source should be read from."""

    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        return self.pages(fetcher, options)

    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        editions: list[Edition] = []
        for document in documents:
            if document.status_code != 200:
                continue
            edition = extract_edition(
                self._classifier,
                source_name=self.profile.name,
                url=document.url,
                content=document.content,
                retrieved_at=document.retrieved_at,
                content_hash=document.content_hash,
                confidence=self.extraction_confidence,
            )
            if edition is not None:
                editions.append(edition)
        return editions
