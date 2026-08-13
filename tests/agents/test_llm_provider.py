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
