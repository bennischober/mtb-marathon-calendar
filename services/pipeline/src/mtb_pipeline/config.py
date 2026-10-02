"""Pipeline configuration file support.

The LLM connection (and any future service settings) lives in a TOML config
file so scheduled runs need no shell exports. Precedence, highest first:

1. CLI flags (``--llm-model`` etc.)
2. ``MTB_LLM_*`` environment variables
3. the config file (``--config``, ``mtb-pipeline.toml`` in the working
   directory, then ``services/pipeline/mtb-pipeline.toml``)

The API key is a secret: it is read into memory only, never logged, never
written to the database or export, and config files containing keys should
stay out of version control (``.gitignore`` covers ``mtb-pipeline.toml``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .llm import LLMConfig

CONFIG_FILENAMES = ("mtb-pipeline.toml",)
#: Project root (services/pipeline) and repo root, relative to this file —
#: config discovery must not depend on the process working directory.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
FALLBACK_DIRS = (_PACKAGE_ROOT, _REPO_ROOT)

#: Environment variable carrying the API key; overrides the config file.
API_KEY_ENV = "MTB_LLM_API_KEY"
#: Environment variables for the other LLM settings (rarely needed).
BASE_URL_ENV = "MTB_LLM_BASE_URL"
MODEL_ENV = "MTB_LLM_MODEL"


class ConfigError(ValueError):
    """The config file exists but is unusable."""


@dataclass(frozen=True)
class PipelineConfig:
    """Resolved pipeline settings."""

    llm: LLMConfig | None
    #: where the values came from, for the status output
    source: str = "none"

    @property
    def llm_configured(self) -> bool:
        return self.llm is not None


def load_pipeline_config(explicit: Path | None = None) -> PipelineConfig:
    """Resolve configuration from file, environment and defaults.

    ``explicit`` is a user-passed ``--config`` path; a missing or invalid
    explicit file is an error. Discovered files (working directory, fallback
    dir) are used when present and valid; a broken discovered file is an
    error too, because silently ignoring it would hide a misconfiguration.
    """
    if explicit is not None:
        return _from_file(explicit, explicit_mode=True)

    for directory in (Path.cwd(), *FALLBACK_DIRS):
        for filename in CONFIG_FILENAMES:
            path = directory / filename
            if path.is_file():
                return _from_file(path, explicit_mode=False)

    return _from_env(source="environment")


def _from_file(path: Path, *, explicit_mode: bool) -> PipelineConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if explicit_mode:
            raise ConfigError(f"config file not found: {path}") from None
        return _from_env(source="environment")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error

    section = raw.get("llm")
    if section is None:
        if explicit_mode:
            raise ConfigError(f"{path}: missing [llm] section")
        return _from_env(source="environment")
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [llm] must be a table")

    base_url = (
        os.environ.get(BASE_URL_ENV, "").strip()
        or str(section.get("base_url") or "").strip()
        or "https://api.openai.com/v1"
    )
    model = os.environ.get(MODEL_ENV, "").strip() or section.get("model")
    if not model:
        if explicit_mode:
            raise ConfigError(f"{path}: [llm] needs a 'model' value")
        return _from_env(source="environment")

    api_key = os.environ.get(API_KEY_ENV, "").strip() or str(section.get("api_key") or "").strip()
    if not api_key:
        if explicit_mode:
            raise ConfigError(
                f"{path}: no API key — set 'api_key' in [llm] or {API_KEY_ENV} in the environment"
            )
        return PipelineConfig(llm=None, source=f"{path} (no API key)")

    return PipelineConfig(
        llm=LLMConfig(
            base_url=str(base_url).rstrip("/"),
            model=str(model),
            api_key=api_key,
        ),
        source=str(path),
    )


def _from_env(*, source: str) -> PipelineConfig:
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    model = os.environ.get(MODEL_ENV, "").strip()
    if not api_key or not model:
        return PipelineConfig(llm=None, source="none")
    base_url = os.environ.get(BASE_URL_ENV, "https://api.openai.com/v1").rstrip("/")
    return PipelineConfig(
        llm=LLMConfig(base_url=base_url, model=model, api_key=api_key),
        source=source,
    )
