"""Config file resolution: TOML loading, precedence and secret handling."""

from pathlib import Path

import pytest

from mtb_pipeline.config import ConfigError, load_pipeline_config
from mtb_pipeline.llm import LLMConfig


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MTB_LLM_API_KEY", "MTB_LLM_BASE_URL", "MTB_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_a_complete_config_file_resolves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "mtb-pipeline.toml",
        '[llm]\nbase_url = "http://llm.local/v1/"\nmodel = "test-model"\napi_key = "key-123"\n',
    )

    config = load_pipeline_config()

    assert config.llm is not None
    assert config.llm.base_url == "http://llm.local/v1"
    assert config.llm.model == "test-model"
    assert config.llm.api_key == "key-123"
    assert config.llm_configured
    assert "mtb-pipeline.toml" in config.source


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "mtb-pipeline.toml",
        '[llm]\nmodel = "file-model"\napi_key = "file-key"\n',
    )
    monkeypatch.setenv("MTB_LLM_API_KEY", "env-key")

    config = load_pipeline_config()

    assert config.llm is not None
    assert config.llm.api_key == "env-key"
    assert config.llm.model == "file-model"


def test_the_cli_layer_receives_resolved_values_to_override(tmp_path, monkeypatch):
    """CLI flags override the config in the command; the file layer only
    needs to expose the resolved values the command can replace."""
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path / "mtb-pipeline.toml",
        '[llm]\nmodel = "file-model"\napi_key = "file-key"\n',
    )

    config = load_pipeline_config()

    assert config.llm is not None
    assert config.llm.model == "file-model"
    assert config.llm.api_key == "file-key"


def test_a_missing_key_degrades_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "mtb-pipeline.toml", '[llm]\nmodel = "test-model"\n')

    config = load_pipeline_config()

    assert config.llm is None
    assert not config.llm_configured


def test_an_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_pipeline_config(tmp_path / "nope.toml")


def test_an_explicit_file_without_llm_section_is_an_error(tmp_path):
    path = _write(tmp_path / "cfg.toml", '[other]\nvalue = 1\n')

    with pytest.raises(ConfigError, match="missing \\[llm\\]"):
        load_pipeline_config(path)


def test_an_explicit_file_without_model_is_an_error(tmp_path):
    path = _write(tmp_path / "cfg.toml", '[llm]\napi_key = "k"\n')

    with pytest.raises(ConfigError, match="needs a 'model'"):
        load_pipeline_config(path)


def test_an_explicit_file_without_key_is_an_error(tmp_path):
    path = _write(tmp_path / "cfg.toml", '[llm]\nmodel = "m"\n')

    with pytest.raises(ConfigError, match="no API key"):
        load_pipeline_config(path)


def test_invalid_toml_is_an_error(tmp_path):
    path = _write(tmp_path / "cfg.toml", "[llm\nbroken")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_pipeline_config(path)


def test_discovered_invalid_toml_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "mtb-pipeline.toml", "[llm\nbroken")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_pipeline_config()


def test_without_any_config_the_pipeline_degrades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Discovery also checks the project root and repo root, which exist in a
    # real checkout; point both away to prove true absence degrades cleanly.
    import mtb_pipeline.config as config_module

    monkeypatch.setattr(config_module, "FALLBACK_DIRS", (tmp_path / "elsewhere",))

    config = load_pipeline_config()

    assert config.llm is None
    assert config.source == "none"


def test_env_only_configuration_works(monkeypatch):
    monkeypatch.setenv("MTB_LLM_API_KEY", "env-key")
    monkeypatch.setenv("MTB_LLM_MODEL", "env-model")
    monkeypatch.setenv("MTB_LLM_BASE_URL", "http://env.local/v1")

    config = load_pipeline_config()

    assert config.llm == LLMConfig(
        base_url="http://env.local/v1", model="env-model", api_key="env-key"
    )
