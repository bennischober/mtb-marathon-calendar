"""Registry of available calendar sources."""

from __future__ import annotations

from .base import CollectOptions, SourceAdapter
from .radsport_events import RadsportEventsAdapter

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    RadsportEventsAdapter.name: RadsportEventsAdapter,
}


def available_sources() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(name: str) -> SourceAdapter:
    try:
        adapter_class = _ADAPTERS[name]
    except KeyError:
        raise ValueError(
            f"unknown source {name!r}; available sources: {', '.join(available_sources())}"
        ) from None
    return adapter_class()


__all__ = [
    "CollectOptions",
    "SourceAdapter",
    "available_sources",
    "get_adapter",
]
