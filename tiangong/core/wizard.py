"""天工安装向导 — 首次运行配置流程。

引导用户:
1. 选择 API 服务商
2. 输入 API Key
3. 选择默认模型
4. 验证连接
5. 写入配置
"""

import os
import re
import sys
from pathlib import Path

import yaml

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.status import Status

console = Console()

GOLD = "#c9a84c"
DIM = "#737373"
ERR = "#ef4444"
OK = "#4ade80"

_STEP = 0
_TOTAL_STEPS = 5

def _step(title: str):
    global _STEP
    _STEP += 1
    console.print()
    console.print(f" [{GOLD}]{_STEP}/{_TOTAL_STEPS}[/{GOLD}] {title}", style="bold")

# 预设的 API 服务商
PROVIDERS = {
    "1": {
        "name": "deepseek",
        "label": "DeepSeek (推荐)",
        "base_url": "https://api.deepseek.com/v1",
        "models": {
            "deepseek-chat": {"display_name": "DeepSeek-V4-pro", "max_tokens": 8192},
        },
        "api_key_env": "DEEPSEEK_API_KEY",
        "signup_url": "https://platform.deepseek.com",
    },
    "2": {
        "name": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": {
            "gpt-4o-mini": {"display_name": "GPT-4o mini", "max_tokens": 8192},
            "gpt-4o": {"display_name": "GPT-4o", "max_tokens": 8192},
        },
        "api_key_env": "OPENAI_API_KEY",
        "signup_url": "https://platform.openai.com",
    },
    "3": {
        "name": "custom",
        "label": "自定义 OpenAI 兼容 API",
        "base_url": "",
        "models": {},
        "api_key_env": "TIANGONG_API_KEY",
        "signup_url": "",
    },
}


def _print_welcome():
    """显示欢迎界面。"""
    console.print()
    console.print(Panel(
        "[bold]  天工 AI 助手[/bold]\n\n"
        "为中国开发者而生的全中文智能编程搭档。\n"
        "开物成务 · 以智慧创造万物。\n\n"
        "[dim]首次使用，需要配置 API 密钥。[/dim]\n"
        "[dim]全程只需 1 分钟。[/dim]",
        border_style=GOLD,
        padding=(1, 2),
    ))


def _select_provider() -> dict:
    """选择 API 服务商。"""
    _step("选择 AI 服务商")
    for key, p in PROVIDERS.items():
        console.print(f"  [{GOLD}]{key}[/{GOLD}]  {p['label']}")

    while True:
        choice = Prompt.ask("\n  输入编号", default="1").strip()
        if choice in PROVIDERS:
            provider = PROVIDERS[choice]
            # 深拷贝
            return {
                "name": provider["name"],
                "label": provider["label"],
                "base_url": provider["base_url"],
                "models": dict(provider["models"]),
                "api_key_env": provider["api_key_env"],
                "signup_url": provider["signup_url"],
            }
        console.print(f"  [{ERR}]请输入 1-{len(PROVIDERS)}[/{ERR}]")


def _input_api_key(provider: dict) -> str:
    """输入 API Key。"""
    _step("配置 API 密钥")
    if provider["signup_url"]:
        console.print(f"[dim]没有密钥？去注册: {provider['signup_url']}[/dim]")
    console.print()

    while True:
        key = Prompt.ask(
            f"  输入 {provider['label']} API Key",
            password=True,
        ).strip()

        if not key:
            console.print(f"  [{ERR}]API Key 不能为空[/{ERR}]")
            continue

        # 基本格式验证
        if provider["name"] == "deepseek" and not key.startswith("sk-"):
            console.print(f"  [{ERR}]DeepSeek API Key 应以 sk- 开头[/{ERR}]")
            if not Confirm.ask("  继续使用此 Key?", default=True):
                continue

        return key


def _select_model(provider: dict) -> str:
    """选择默认模型。"""
    if len(provider["models"]) <= 1:
        return list(provider["models"].keys())[0] if provider["models"] else ""

    _step("选择默认模型")
    models = list(provider["models"].items())
    for i, (key, info) in enumerate(models, 1):
        console.print(f"  [{GOLD}]{i}[/{GOLD}]  {info.get('display_name', key)}")

    while True:
        choice = Prompt.ask("\n  输入编号", default="1").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx][0]
        except ValueError:
            pass
        console.print(f"  [{ERR}]请输入 1-{len(models)}[/{ERR}]")


def _input_custom(provider: dict) -> dict:
    """配置自定义 API。"""
    _step("配置自定义 API")
    base_url = Prompt.ask("  API Base URL", default="https://api.openai.com/v1").strip()
    model_name = Prompt.ask("  模型名称", default="gpt-4o-mini").strip()
    display = Prompt.ask("  模型显示名", default=model_name).strip()

    provider["base_url"] = base_url
    provider["models"] = {
        model_name: {"display_name": display, "max_tokens": 8192},
    }
    return provider


def _test_connection(provider: dict, api_key: str, model: str) -> bool:
    """测试 API 连接。"""
    _step("测试连接")

    try:
        from openai import OpenAI

        with Status("[dim]正在连接 API 服务器...[/dim]", console=console, spinner="dots"):
            client = OpenAI(api_key=api_key, base_url=provider["base_url"])
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "回复OK"}],
                max_tokens=10,
            )
        reply = r.choices[0].message.content
        console.print(f"  [{OK}]✓ 连接成功！AI 回复: {reply}[/{OK}]")
        return True
    except Exception as e:
        err_msg = str(e)[:200]
        console.print(f"  [{ERR}]✗ 连接失败: {err_msg}[/{ERR}]")
        return False


def _write_config(provider: dict, api_key: str, default_model: str):
    """写入 config.yaml。"""
    config_path = Path(os.environ.get("TIANGONG_HOME", Path.home() / ".tiangong"))

    # 构建模型配置
    models_config = {}
    for model_key, info in provider["models"].items():
        config_key = model_key.replace("-", "_").replace(".", "_")
        models_config[config_key] = {
            "name": model_key,
            "display_name": info.get("display_name", model_key),
            "max_tokens": info.get("max_tokens", 8192),
        }

    first_model_key = list(models_config.keys())[0] if models_config else "default"

    config = {
        "model": {
            "default": first_model_key,
            "provider": provider["name"],
            "base_url": provider["base_url"],
        },
        "providers": {
            provider["name"]: {
                "api_key_env": provider["api_key_env"],
                "models": models_config,
            }
        },
        "agent": {
            "name": "天工",
            "max_turns": 60,
            "timeout": 1800,
            "personality": (
                "你是「天工 AI 助手」，一个专业、高效、为中国开发者而生的全中文智能编程搭档。\n\n"
                "## 身份\n"
                "- 你的名字是\"天工\"，由 chengzi-AI 团队创造\n"
                "- 你运行在用户的操作系统上，是用户的技术搭档\n\n"
                "## 性格\n"
                "- 专业、沉稳、可靠；不废话但也不冷冰冰\n"
                "- 像一位技术深厚的前辈搭档，给建议时有理有据\n\n"
                "## 核心规则（必须遵守）\n"
                "- 只回答用户最新的问题，不要在回复中逐条回顾或重复回答之前已经回答过的历史问题\n"
                "- 如果之前的回答已经覆盖了某个问题，不要再重复说一遍\n"
                "- 简洁优先：先说结论，再说细节。不要铺垫\n"
                "- 能直接做的就别问，结果导向\n"
                "- 主动提醒潜在问题\n"
                "- 用中文回复，代码和技术名词保留原样"
            ),
        },
        "memory": {"enabled": True, "memory_char_limit": 5000, "user_char_limit": 3000},
        "voice": {"enabled": True, "tts_provider": "edge", "tts_voice": "zh-CN-female", "auto_speak": False},
        "computer_use": {"enabled": True},
        "logging": {"level": "WARNING"},
    }

    config_file = config_path / "config.yaml"
    config_path.mkdir(parents=True, exist_ok=True)

    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # 设置环境变量持久化
    shell_rc = Path.home() / ".zshrc"
    env_line = f'\nexport {provider["api_key_env"]}="{api_key}"  # 天工 AI 助手\n'

    try:
        existing = shell_rc.read_text() if shell_rc.exists() else ""
        if f"export {provider['api_key_env']}" not in existing:
            with open(shell_rc, "a") as f:
                f.write(env_line)
            console.print(f"  [dim]API Key 已写入 ~/.zshrc[/dim]")
    except Exception:
        pass

    # 设置当前会话的 API Key
    os.environ[provider["api_key_env"]] = api_key

    console.print()
    console.print(Panel(
        f"[{OK}]✅ 配置完成！[/{OK}]\n\n"
        f"配置文件: {config_file}\n"
        f"API Key: {provider['api_key_env']}\n"
        f"模型: {provider['models'][default_model].get('display_name', default_model)}\n\n"
        f"[dim]运行 [{GOLD}]tiangong[/{GOLD}] 启动天工 AI 助手[/dim]",
        border_style=GOLD,
        padding=(1, 2),
    ))

    return config_file


def run_wizard():
    """运行安装向导主流程。"""
    _print_welcome()

    # 1. 选择服务商
    provider = _select_provider()

    # 2. 自定义 API
    if provider["name"] == "custom":
        provider = _input_custom(provider)

    # 3. 输入 API Key
    api_key = _input_api_key(provider)

    # 4. 选择模型
    if provider["models"]:
        default_model = _select_model(provider)
    else:
        default_model = list(provider["models"].keys())[0]

    # 5. 测试连接
    ok = _test_connection(provider, api_key, default_model)
    if not ok:
        console.print(f"\n[{ERR}]连接测试失败，但仍可保存配置。[/{ERR}]")
        if not Confirm.ask("  是否保存配置并继续?", default=True):
            console.print("  已取消。可以重新运行 tiangong --setup 配置。")
            sys.exit(0)

    # 6. 写入配置
    _write_config(provider, api_key, default_model)


def needs_setup() -> bool:
    """检查是否需要运行安装向导。"""
    config_path = Path(os.environ.get("TIANGONG_HOME", Path.home() / ".tiangong")) / "config.yaml"

    if not config_path.exists():
        return True

    # 检查是否有有效的 API Key
    try:
        data = yaml.safe_load(config_path.read_text())
        provider_name = data.get("model", {}).get("provider", "")
        providers = data.get("providers", {})
        provider = providers.get(provider_name, {})
        env_key = provider.get("api_key_env", "")

        if env_key and os.environ.get(env_key):
            return False

        # 检查是否有任何可用的 API Key
        for p_name, p_info in providers.items():
            if os.environ.get(p_info.get("api_key_env", "")):
                return False

        return True
    except Exception:
        return True
