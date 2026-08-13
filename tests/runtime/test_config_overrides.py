from src.config.config import config


def test_game_master_env_overrides_model_and_base_url(monkeypatch):
    monkeypatch.setenv("GM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("GM_MODEL_BASE_URL", "https://example.test/claude")
    monkeypatch.setenv("GM_API_KEY", "gm-test-key")

    cfg = config.get_component_config("game_master")

    assert cfg["model"] == "claude-sonnet-4-6"
    assert cfg["base_url"] == "https://example.test/claude"
    assert cfg["api_key"] == "gm-test-key"


def test_agent_env_overrides_model_and_base_url(monkeypatch):
    monkeypatch.setenv("ACTOR_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("ACTOR_MODEL_BASE_URL", "https://example.test/actors")
    monkeypatch.setenv("ACTOR_API_KEY", "actor-test-key")

    cfg = config.get_component_config("agent")

    assert cfg["model"] == "openai/gpt-4.1-mini"
    assert cfg["base_url"] == "https://example.test/actors"
    assert cfg["api_key"] == "actor-test-key"


def test_narrator_prefers_dedicated_env_and_falls_back_to_gm(monkeypatch):
    monkeypatch.delenv("NARRATOR_MODEL", raising=False)
    monkeypatch.delenv("NARRATOR_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("NARRATOR_API_KEY", raising=False)
    monkeypatch.setenv("GM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("GM_MODEL_BASE_URL", "https://example.test/gm")
    monkeypatch.setenv("GM_API_KEY", "gm-test-key")

    cfg = config.get_component_config("narrator")

    assert cfg["model"] == "claude-sonnet-4-6"
    assert cfg["base_url"] == "https://example.test/gm"
    assert cfg["api_key"] == "gm-test-key"

    monkeypatch.setenv("NARRATOR_MODEL", "openai/gpt-4.1")
    monkeypatch.setenv("NARRATOR_MODEL_BASE_URL", "https://example.test/narrator")
    monkeypatch.setenv("NARRATOR_API_KEY", "narrator-test-key")

    cfg = config.get_component_config("narrator")

    assert cfg["model"] == "openai/gpt-4.1"
    assert cfg["base_url"] == "https://example.test/narrator"
    assert cfg["api_key"] == "narrator-test-key"
