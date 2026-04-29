"""LLM Client — 多 Provider 支持的门面层。[原创]

支持后端:
- DeepSeek / OpenAI / Anthropic / GLM / Kimi / Alibaba / OpenRouter
- Ollama (本地模型)
- MLX-LM (Apple Silicon 本地模型)

通过 model_key 自动路由到正确的 Provider 和模型。
"""

import logging
from typing import Any, Dict, List, Generator

from .config import Config, ConfigError
from .llm_provider import (
    BaseLLMProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    MLXLMProvider,
    create_provider,
)

logger = logging.getLogger(__name__)


def _resolve_model(model_key: str | None = None) -> tuple[str, str, dict]:
    """根据 model_key 在所有 Provider 中查找模型，返回 (provider_name, model_api_name, model_info)。"""
    config = Config.get()
    providers = config.provider_config

    if model_key is None:
        model_key = config.default_model

    # 遍历所有 Provider 查找 model_key
    for provider_name, provider_data in providers.items():
        models = provider_data.get("models", {})
        if model_key in models:
            model_info = models[model_key]
            return provider_name, model_info.get("name", model_key), model_info

    # 未找到，使用默认 Provider
    default_provider = config.model_config.get("provider", "deepseek")
    logger.warning("未找到模型 '%s'，回退到默认 Provider '%s'", model_key, default_provider)
    return default_provider, model_key, {}


def _build_provider(model_key: str | None = None) -> BaseLLMProvider:
    """根据 model_key 构建对应的 Provider 实例。"""
    provider_name, model_name, model_info = _resolve_model(model_key)
    config = Config.get()
    provider_data = config.get_provider(provider_name)

    provider_type = provider_data.get("type", "openai")
    api_key = provider_data.get("api_key", "")
    base_url = provider_data.get("base_url", "") or config.model_config.get("base_url", "")
    max_tokens = model_info.get("max_tokens", 8192)

    if provider_type in ("ollama",):
        return OllamaProvider(
            model=model_name,
            base_url=base_url or "http://localhost:11434/v1",
            max_tokens=max_tokens,
        )
    elif provider_type in ("mlx", "mlx-lm"):
        return MLXLMProvider(
            model=model_name,
            base_url=base_url or "http://localhost:8080/v1",
            max_tokens=max_tokens,
        )
    else:
        # OpenAI 兼容 (深度寻求 / OpenAI / Anthropic / GLM / Kimi / 阿里 / OpenRouter 等)
        if not base_url:
            raise ConfigError(
                f"Provider '{provider_name}' 缺少 base_url 配置。\n"
                f"请在 config.yaml 的 providers.{provider_name}.base_url 或 model.base_url 中设置 API 端点。\n"
                f"示例: base_url: https://api.deepseek.com/v1"
            )
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            max_tokens=max_tokens,
        )


class LLMClient:
    """LLM 客户端门面——根据配置自动选择 Provider。

    使用示例:
        client = LLMClient()  # 默认模型
        client = LLMClient(model_key="glm4_flash")  # 切换到 GLM-4-Flash
        client = LLMClient(model_key="or_claude")   # 切换到 OpenRouter-Claude
    """

    def __init__(self, model_key: str | None = None):
        self._model_key = model_key
        self._provider = _build_provider(model_key)

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def client(self):
        """暴露底层 client，兼容旧代码（如 context_compressor 的 llm_summarize_messages）。"""
        if hasattr(self._provider, "client"):
            return self._provider.client
        raise AttributeError("当前 Provider 没有直接 client 对象")

    def chat(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Dict[str, Any]:
        return self._provider.chat(messages, tools=tools)

    def chat_stream(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Generator[Dict[str, Any], None, None]:
        yield from self._provider.chat_stream(messages, tools=tools)
