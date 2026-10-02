"""Registry of available calendar sources.

The registry is the single place that knows which sources exist, so the status
report and any future multi-source run enumerate adapters from here instead of
hard-coding names.
"""

from __future__ import annotations

from .base import CollectOptions, SourceAdapter, SourceProfile
from .bawue_radsportverband import BadenWuerttembergCalendarAdapter
from .radsport_events import RadsportEventsAdapter

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    RadsportEventsAdapter.profile.name: RadsportEventsAdapter,
    BadenWuerttembergCalendarAdapter.profile.name: BadenWuerttembergCalendarAdapter,
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


def get_profile(name: str) -> SourceProfile:
    return get_adapter(name).profile


def all_profiles() -> list[SourceProfile]:
    """Every registered profile, strongest source priority first."""
    profiles = [adapter_class.profile for adapter_class in _ADAPTERS.values()]
    return sorted(profiles, key=lambda profile: (profile.priority_rank, profile.name))


__all__ = [
    "CollectOptions",
    "SourceAdapter",
    "SourceProfile",
    "all_profiles",
    "available_sources",
    "get_adapter",
    "get_profile",
]
