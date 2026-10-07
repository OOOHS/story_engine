from src.story_engine.llm.provider import LLMProvider


def test_custom_gateway_without_provider_prefix_is_normalized_to_openai_compat():
    provider = LLMProvider(
        model="claude-sonnet-4-6",
        base_url="https://www.right.codes/claude-aws",
        api_key="test-key",
    )

    assert provider.model == "openai/claude-sonnet-4-6"
    assert provider.base_url == "https://www.right.codes/claude-aws/v1"


def test_openai_compat_base_url_keeps_existing_v1_suffix():
    provider = LLMProvider(
        model="openai/claude-sonnet-4-6",
        base_url="https://www.right.codes/claude-aws/v1",
        api_key="test-key",
    )

    assert provider.model == "openai/claude-sonnet-4-6"
    assert provider.base_url == "https://www.right.codes/claude-aws/v1"


def test_provider_qualified_models_are_not_rewritten():
    provider = LLMProvider(
        model="deepseek/deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )

    assert provider.model == "deepseek/deepseek-chat"
    assert provider.base_url == "https://api.deepseek.com"


def test_persistent_agent_provider_forwards_full_history_and_proposal_tools(monkeypatch):
    from types import SimpleNamespace
    import src.story_engine.llm.provider as provider_module

    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="继续观察。", role="assistant", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(provider_module.litellm, "completion", completion)
    history = [
        {"role": "system", "content": "导演"},
        {"role": "user", "content": "玩家发现信件"},
        {"role": "assistant", "content": "留意调查"},
        {"role": "user", "content": "玩家读出了寄件人"},
    ]
    tools = [{"type": "function", "function": {"name": "propose_storylet", "parameters": {"type": "object"}}}]
    response = LLMProvider(api_key="test-key").generate_messages(history, tools=tools)
    assert captured["messages"] == history
    assert captured["tools"] == tools
    assert response["content"] == "继续观察。"
