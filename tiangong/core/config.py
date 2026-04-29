"""天工 configuration loader."""

import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml


TIANGONG_HOME = Path(os.environ.get("TIANGONG_HOME", Path.home() / ".tiangong"))
CONFIG_PATH = TIANGONG_HOME / "config.yaml"


class ConfigError(Exception):
    """配置错误。"""
    pass


class Config:
    """Singleton config loaded from config.yaml."""

    _instance: "Config | None" = None

    def __init__(self):
        if not CONFIG_PATH.exists():
            raise ConfigError(
                f"配置文件不存在: {CONFIG_PATH}\n"
                f"请运行 'tiangong --setup' 完成首次配置。"
            )
        try:
            self._data = yaml.safe_load(CONFIG_PATH.read_text())
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件解析失败: {e}")

        if self._data is None:
            self._data = {}
        if not isinstance(self._data, dict):
            raise ConfigError("配置文件格式错误：需要一个 YAML 字典（mapping）。")

    @classmethod
    def get(cls) -> "Config":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def model_config(self) -> Dict[str, Any]:
        return self._data.get("model", {})

    @property
    def default_model(self) -> str:
        return self.model_config.get("default", "deepseek-v4-flash")

    @property
    def provider_config(self) -> Dict[str, Any]:
        return self._data.get("providers", {})

    @property
    def agent_config(self) -> Dict[str, Any]:
        return self._data.get("agent", {})

    @property
    def memory_config(self) -> Dict[str, Any]:
        return self._data.get("memory", {})

    @property
    def voice_config(self) -> Dict[str, Any]:
        return self._data.get("voice", {})

    @property
    def computer_config(self) -> Dict[str, Any]:
        return self._data.get("computer_use", {})

    def get_provider(self, provider_name: str | None = None) -> Dict[str, Any]:
        """Get active provider config with API key resolved."""
        if provider_name is None:
            provider_name = self.model_config.get("provider", "deepseek")
        provider = dict(self.provider_config.get(provider_name, {}))
        if "api_key_env" in provider:
            env_val = os.environ.get(provider["api_key_env"], "")
            if env_val:
                provider["api_key"] = env_val
            elif not provider.get("api_key"):
                provider["api_key"] = ""
        return provider

    def get_model_name(self, model_key: str) -> str:
        """将逻辑模型 key 解析为 API 模型名。"""
        provider = self.get_provider()
        models = provider.get("models", {})
        model_info = models.get(model_key, {})
        return model_info.get("name", model_key)

    def get_model_display_name(self, model_key: str | None = None) -> str:
        """获取模型的中文显示名称。"""
        if model_key is None:
            model_key = self.default_model
        provider = self.get_provider()
        models = provider.get("models", {})
        model_info = models.get(model_key, {})
        return model_info.get("display_name", model_key)

    def get_memory_dir(self) -> Path:
        return TIANGONG_HOME / "data"

    def get_skills_dir(self) -> Path:
        return TIANGONG_HOME / "skills"
