"""LLM Provider 抽象层 — 支持 OpenAI 兼容 / Ollama / MLX-LM 等多后端。[原创]

每个 Provider 实现 chat() 和 chat_stream() 两个核心接口。
LLMClient 作为 facade，根据配置选择合适的 Provider。
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Generator

from openai import OpenAI

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类。"""

    @abstractmethod
    def chat(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Dict[str, Any]:
        """非流式对话。"""
        ...

    @abstractmethod
    def chat_stream(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Generator[Dict[str, Any], None, None]:
        """流式对话，逐 token 产出。"""
        ...


class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI 兼容 API Provider — 支持 DeepSeek / OpenAI / 任意兼容端点。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 8192,
        **kwargs,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url, **kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def chat(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        result: Dict[str, Any] = {
            "role": msg.role,
            "content": msg.content or "",
            "finish_reason": choice.finish_reason,
        }

        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]

        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result["reasoning"] = msg.reasoning_content

        usage = response.usage
        if usage:
            logger.debug("Tokens: in=%d out=%d", usage.prompt_tokens, usage.completion_tokens)
            result["usage"] = {
                "prompt_tokens": usage.prompt_tokens or 0,
                "completion_tokens": usage.completion_tokens or 0,
            }

        return result

    def chat_stream(self, messages: List[Dict[str, Any]], tools: List[dict] | None = None) -> Generator[Dict[str, Any], None, None]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception:
            # 部分 Provider 不支持 stream_options，回退重试
            del kwargs["stream_options"]
            try:
                stream = self.client.chat.completions.create(**kwargs)
            except Exception:
                raise  # 回退后仍失败，向上抛出

        tool_call_buf: Dict[int, Dict[str, Any]] = {}
        content_buf = ""
        final_usage = None

        for chunk in stream:
            # 部分 Provider 在最后的 chunk 中返回 usage
            if hasattr(chunk, "usage") and chunk.usage:
                final_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                }

            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                content_buf += delta.content
                yield {"content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"index": idx, "id": tc.id or "", "function": {"name": "", "arguments": ""}}
                    if tc.id:
                        tool_call_buf[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_buf[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_buf[idx]["function"]["arguments"] += tc.function.arguments

        if tool_call_buf:
            tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": tc["function"],
                }
                for tc in sorted(tool_call_buf.values(), key=lambda x: x["index"] if "index" in x else 0)
            ]
            yield {"_tool_calls": tool_calls}

        if final_usage:
            yield {"_usage": final_usage}


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama 本地模型 Provider。

    Ollama 默认在 http://localhost:11434/v1 提供 OpenAI 兼容 API。
    """

    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1",
                 max_tokens: int = 4096, **kwargs):
        super().__init__(
            api_key="ollama",  # Ollama 不需要真实 API key
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            **kwargs,
        )
        logger.info("Ollama Provider: %s @ %s", model, base_url)


class MLXLMProvider(OpenAICompatibleProvider):
    """MLX-LM 本地模型 Provider。

    MLX-LM 服务默认在 http://localhost:8080/v1 提供 OpenAI 兼容 API。
    """

    def __init__(self, model: str, base_url: str = "http://localhost:8080/v1",
                 max_tokens: int = 4096, **kwargs):
        super().__init__(
            api_key="mlx",  # MLX-LM 不需要真实 API key
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            **kwargs,
        )
        logger.info("MLX-LM Provider: %s @ %s", model, base_url)


# ── Provider 工厂 ──────────────────────────────────────

_PROVIDER_REGISTRY: Dict[str, type] = {
    "openai": OpenAICompatibleProvider,
    "deepseek": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
    "mlx": MLXLMProvider,
    "mlx-lm": MLXLMProvider,
}


def create_provider(provider_config: dict, model_name: str = "", max_tokens: int = 8192) -> BaseLLMProvider:
    """根据配置创建 Provider 实例。

    provider_config 格式 (来自 config.yaml providers.<name>):
    {
        "type": "deepseek",           # provider 类型
        "api_key_env": "DEEPSEEK_KEY",# API key 环境变量（可选，自动解析）
        "api_key": "***",             # 或直接提供 key
        "base_url": "https://...",    # API 端点
    }
    """
    import os

    provider_type = provider_config.get("type", "openai")
    api_key = provider_config.get("api_key", "")
    base_url = provider_config.get("base_url", "")

    # 解析 api_key_env（与 Config.get_provider() 保持一致）
    if "api_key_env" in provider_config:
        env_val = os.environ.get(provider_config["api_key_env"], "")
        if env_val:
            api_key = env_val

    cls = _PROVIDER_REGISTRY.get(provider_type)
    if cls is None:
        logger.warning("未知 Provider 类型 '%s'，回退到 OpenAICompatibleProvider", provider_type)
        cls = OpenAICompatibleProvider

    if cls is OpenAICompatibleProvider:
        return cls(api_key=api_key, base_url=base_url, model=model_name, max_tokens=max_tokens)

    # 子类（Ollama/MLX）用各自默认参数，忽略外部 api_key
    kwargs = {"model": model_name, "max_tokens": max_tokens}
    if base_url:
        kwargs["base_url"] = base_url
    return cls(**kwargs)


def register_provider(name: str, cls: type):
    """注册自定义 Provider 类型。"""
    _PROVIDER_REGISTRY[name] = cls
