"""测试 LLM Provider 抽象: 工厂创建 + 接口一致性。[原创]"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from tiangong.core.llm_provider import (
    BaseLLMProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    MLXLMProvider,
    create_provider,
    register_provider,
    _PROVIDER_REGISTRY,
)


class TestProviderRegistry:
    def test_default_providers_registered(self):
        assert "openai" in _PROVIDER_REGISTRY
        assert "deepseek" in _PROVIDER_REGISTRY
        assert "ollama" in _PROVIDER_REGISTRY
        assert "mlx" in _PROVIDER_REGISTRY
        assert "mlx-lm" in _PROVIDER_REGISTRY

    def test_register_custom_provider(self):
        class CustomProvider(BaseLLMProvider):
            def chat(self, messages, tools=None):
                return {"role": "assistant", "content": "custom"}

            def chat_stream(self, messages, tools=None):
                yield {"content": "custom"}

        register_provider("custom_test", CustomProvider)
        assert "custom_test" in _PROVIDER_REGISTRY
        # 清理
        del _PROVIDER_REGISTRY["custom_test"]


class TestOpenAICompatibleProvider:
    def test_create_minimal(self):
        p = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://test.api/v1",
            model="test-model",
        )
        assert p.model == "test-model"
        assert p.max_tokens == 8192

    def test_create_custom_max_tokens(self):
        p = OpenAICompatibleProvider(
            api_key="k", base_url="http://x", model="m",
            max_tokens=4096,
        )
        assert p.max_tokens == 4096


class TestOllamaProvider:
    def test_default_url(self):
        p = OllamaProvider(model="llama3")
        assert p.model == "llama3"
        assert "11434" in str(p.client.base_url)

    def test_custom_url(self):
        p = OllamaProvider(model="mistral", base_url="http://gpu:11434/v1")
        assert "gpu" in str(p.client.base_url)


class TestMLXLMProvider:
    def test_default_url(self):
        p = MLXLMProvider(model="mlx-community/Qwen2.5-7B-Instruct")
        assert "8080" in str(p.client.base_url)

    def test_custom_url(self):
        p = MLXLMProvider(model="local-model", base_url="http://0.0.0.0:9999/v1")
        assert "9999" in str(p.client.base_url)


class TestCreateProvider:
    def test_openai_type(self):
        p = create_provider({
            "type": "openai",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
        })
        assert isinstance(p, OpenAICompatibleProvider)
        assert not isinstance(p, OllamaProvider)

    def test_deepseek_type(self):
        p = create_provider({
            "type": "deepseek",
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com/v1",
        })
        assert isinstance(p, OpenAICompatibleProvider)

    def test_ollama_type(self):
        p = create_provider({
            "type": "ollama",
            "base_url": "http://localhost:11434/v1",
        })
        assert isinstance(p, OllamaProvider)

    def test_mlx_type(self):
        p = create_provider({
            "type": "mlx",
            "base_url": "http://localhost:8080/v1",
        })
        assert isinstance(p, MLXLMProvider)

    def test_mlx_lm_type(self):
        p = create_provider({
            "type": "mlx-lm",
            "base_url": "http://localhost:8080/v1",
        })
        assert isinstance(p, MLXLMProvider)

    def test_unknown_type_fallback(self):
        p = create_provider({
            "type": "unknown_backend",
            "api_key": "k", "base_url": "http://x",
        })
        assert isinstance(p, OpenAICompatibleProvider)


class TestProviderInterface:
    """确保所有 Provider 满足抽象接口。"""

    def test_openai_provider_has_chat(self):
        p = OpenAICompatibleProvider(api_key="k", base_url="http://x", model="m")
        assert callable(p.chat)
        assert callable(p.chat_stream)

    def test_ollama_provider_has_chat(self):
        p = OllamaProvider(model="llama3")
        assert callable(p.chat)
        assert callable(p.chat_stream)

    def test_mlx_provider_has_chat(self):
        p = MLXLMProvider(model="local")
        assert callable(p.chat)
        assert callable(p.chat_stream)
