"""Registry of available calendar sources.

The registry is the single place that knows which sources exist, so the status
report and any future multi-source run enumerate adapters from here instead of
hard-coding names.
"""

from __future__ import annotations

from .base import CollectOptions, SourceAdapter, SourceProfile
from ..llm import LLMClassifier
from .bawue_radsportverband import BadenWuerttembergCalendarAdapter
from .epic_series import EpicSeriesAdapter
from .ffc_calendar import FFCCalendarSource
from .radsport_events import RadsportEventsAdapter

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    RadsportEventsAdapter.profile.name: RadsportEventsAdapter,
    BadenWuerttembergCalendarAdapter.profile.name: BadenWuerttembergCalendarAdapter,
    EpicSeriesAdapter.profile.name: EpicSeriesAdapter,
    FFCCalendarSource.profile.name: FFCCalendarSource,
}


def available_sources() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(name: str, *, classifier: LLMClassifier | None = None) -> SourceAdapter:
    """Instantiate a source adapter.

    Generic LLM-extraction sources need the configured classifier; a missing
    configuration is reported as a clear error rather than a constructor
    TypeError.
    """
    try:
        adapter_class = _ADAPTERS[name]
    except KeyError:
        raise ValueError(
            f"unknown source {name!r}; available sources: {', '.join(available_sources())}"
        ) from None
    import inspect

    if inspect.signature(adapter_class.__init__).parameters.keys() & {"classifier"}:
        if classifier is None:
            from ..config import load_pipeline_config

            config = load_pipeline_config()
            if config.llm is None:
                raise ValueError(
                    f"source {name!r} needs the LLM: configure [llm] in "
                    "mtb-pipeline.toml or set MTB_LLM_* environment variables"
                )
            classifier = LLMClassifier(config.llm)
        return adapter_class(classifier=classifier)
    return adapter_class()


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
]
