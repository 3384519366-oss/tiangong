"""天工 AI 助手 Agent — 模块化对话循环 + 流式输出 + 工具调用。[H+CC融合]

借鉴 Hermes: 并行工具执行、迭代预算、中断引导
借鉴 Claude Code: 任务管理、类型化记忆、流式UI
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Generator

from .config import Config
from .llm_client import LLMClient
from .registry import registry
from .iteration_budget import IterationBudget
from .tool_executor import ToolExecutor
from .context_compressor import ContextCompressor, llm_summarize_messages
from .sub_agent import SubAgentPool
from .session_store import get_session_store, SessionStore
from .code_agent import get_code_executor, extract_code_blocks, has_code_blocks
from ..memory.memory_manager import get_memory_manager
from ..skills.loader import get_skill_registry

logger = logging.getLogger(__name__)


def _discover_tools():
    """AST-free 工具发现：导入 tools/ 和 memory/ 下的模块。"""
    from pathlib import Path
    import importlib
    agent_dir = Path(__file__).resolve().parent.parent
    for subdir in ["tools", "memory"]:
        dirpath = agent_dir / subdir
        if not dirpath.exists():
            continue
        for path in sorted(dirpath.glob("*.py")):
            if path.name.startswith("_") or path.name in (
                "registry.py", "store.py", "consolidator.py", "retriever.py"
            ):
                continue
            rel = path.relative_to(agent_dir)
            parts = list(rel.parts)
            parts[-1] = parts[-1].replace(".py", "")
            mod_name = "tiangong." + ".".join(parts)
            try:
                importlib.import_module(mod_name)
            except Exception as e:
                logger.warning("Failed to import %s: %s", mod_name, e)


class TianGongAgent:
    """天工 AI 助手 — 为中国开发者而生，融合三大顶级 AI 框架精华。"""

    def __init__(self, model_key: str | None = None):
        config = Config.get()
        self.config = config
        self.agent_config = config.agent_config
        self.name = self.agent_config.get("name", "天工")
        self.max_turns = self.agent_config.get("max_turns", 60)
        self.personality = self.agent_config.get("personality",
            "你是天工AI助手。只回答最新问题，不要重复回顾历史。简洁优先，先说结论。")
        self.voice_config = config.voice_config
        self.auto_speak = self.voice_config.get("auto_speak", False)

        self.session_id = uuid.uuid4().hex[:12]
        self.llm = LLMClient(model_key=model_key)

        _discover_tools()

        self.memory_enabled = config.memory_config.get("enabled", True)
        if self.memory_enabled:
            self.memory = get_memory_manager()

        # 技能引擎 [H+CC]
        self.skill_registry = get_skill_registry()
        self.skill_registry.load_all()

        # 核心组件 [H+CC]
        self.budget = IterationBudget(max_iterations=self.max_turns)
        self.tool_executor = ToolExecutor()
        self.compressor = ContextCompressor()  # [CC] 上下文压缩
        self.sub_agent_pool = SubAgentPool(self.llm, self.llm.model)  # [CC] 子代理池
        self.session_store = get_session_store()  # [原创] 会话持久化
        self.code_executor = get_code_executor()  # [smolagents] Code Agent
        self._steer_message: str | None = None  # [H] 引导消息

        # 自动保存计数器
        self._turn_counter = 0
        self._session_name = ""

        # 注入共享子代理池到委派工具
        from ..tools.delegate_tool import set_delegate_pool
        set_delegate_pool(self.sub_agent_pool)

        # MCP 服务器自动连接 [原创]
        self._init_mcp(config)

        # 代码库索引器 [原创]
        self._init_code_indexer(config)

        self.system_prompt = self._build_system_prompt()
        self.messages: List[Dict[str, Any]] = []
        self.all_messages: List[Dict[str, Any]] = []

    def _build_system_prompt(self) -> str:
        # 【强制约束】必须放在最前面，确保 LLM 看到
        force_rules = (
            "【硬规则】\n"
            "1. 不自我介绍，不开场白。\n"
            "2. 不回顾、不重复、不总结历史消息。\n"
            "3. 只回应用户最新一条消息。\n"
        )

        parts = [force_rules, self.personality,
                 f"当前系统: macOS，底层模型: {self.llm.model}"]

        # [smolagents] Code Agent 提示
        parts.append(
            "## 代码执行能力\n"
            "你可以使用 Python 代码块来批量执行操作、处理数据或进行计算。\n"
            "用 ```python ... ``` 包裹代码。可用的工具函数与工具列表相同（将工具名中的 - 替换为 _）。\n"
            "例如: ```python\nfor f in ['a.txt', 'b.txt']:\n    bash(command=f'cat {f}')\n```\n"
            "调用 final_answer(result) 结束代码执行并返回结果。"
        )

        if self.memory_enabled and hasattr(self, "memory"):
            ctx = self.memory.get_system_prompt_context(max_chars=3000)
            if ctx:
                parts.append(ctx)

        # 代码库索引工具 [原创]
        parts.append(
            "## 代码库理解\n"
            "使用 `codebase` 工具查询当前项目代码库:\n"
            "- `symbols`: 按名称搜索函数/类/变量定义\n"
            "- `references`: 查找符号的所有引用位置\n"
            "- `file`: 查看文件中的符号结构\n"
            "- `search`: 全文搜索代码内容\n"
            "首次使用时会自动索引项目。"
        )

        # 技能第1层: 名称+描述 [H]
        skill_prompt = self.skill_registry.get_tier1_prompt() if hasattr(self, "skill_registry") else ""
        if skill_prompt:
            parts.append(skill_prompt)

        return "\n\n".join(parts)

    def _init_mcp(self, config):
        """自动连接 config.yaml 中配置的 MCP 服务器。[原创]"""
        mcp_config = config.data.get("mcp", {})
        servers = mcp_config.get("servers", [])
        if not servers:
            return

        from .mcp_client import get_mcp_client
        mcp = get_mcp_client()
        for srv in servers:
            name = srv.get("name", "")
            command = srv.get("command", "")
            args = srv.get("args", [])
            env = srv.get("env", None)
            if not name or not command:
                continue
            cmd_list = [command] + args
            try:
                ok = mcp.add_server(name, cmd_list, env=env)
                if ok:
                    logger.info("MCP [%s] 已连接", name)
                else:
                    logger.warning("MCP [%s] 连接失败", name)
            except Exception as e:
                logger.warning("MCP [%s] 连接异常: %s", name, e)

    def _init_code_indexer(self, config):
        """后台索引当前工作目录的代码库。[原创]"""
        codebase_config = config.data.get("codebase", {})
        if not codebase_config.get("enabled", True):
            return
        try:
            from .code_indexer import get_indexer
            root = codebase_config.get("root_dir", os.getcwd())
            self.code_indexer = get_indexer(root)
            # 后台线程执行索引，避免阻塞启动
            import threading
            def _bg_index():
                try:
                    result = self.code_indexer.index()
                    logger.info("代码索引完成: %d 文件, %d 符号",
                                result.get("total", 0), result.get("stats", {}).get("symbols", 0))
                except Exception as e:
                    logger.debug("后台代码索引失败: %s", e)
            t = threading.Thread(target=_bg_index, daemon=True)
            t.start()
        except Exception as e:
            logger.debug("代码索引器初始化失败: %s", e)

    # ── 工具层 ──────────────────────────────────────────

    def _get_tools(self) -> List[dict]:
        return registry.get_schemas()

    def _run_tool(self, name: str, args: dict) -> str:
        return registry.dispatch(name, args)

    # ── 上下文压缩 [CC] ──────────────────────────────────

    def _summarize_with_llm(self, messages: list) -> str:
        """使用当前 LLM 摘要中间轮次。"""
        provider = self.config.get_provider()
        return llm_summarize_messages(
            messages,
            self.llm.client,
            self.llm.model,
        )

    def _maybe_compress(self, messages: List[Dict[str, Any]]) -> tuple:
        """检查并压缩上下文，返回 (消息列表, 是否压缩)。"""
        if not self.compressor.needs_compression(messages):
            return messages, False

        logger.info("触发上下文压缩 (使用率: %.1f%%)",
                    self.compressor.get_usage_ratio(messages) * 100)

        return self.compressor.compress(
            messages,
            llm_summarize=self._summarize_with_llm,
        )

    # ── Code Agent [smolagents] ───────────────────────────

    def _handle_code_blocks(self, text: str) -> str | None:
        """提取并执行响应中的代码块。

        返回: 执行结果摘要（如果有代码），否则 None
        """
        if not has_code_blocks(text):
            return None

        blocks = extract_code_blocks(text)
        if not blocks:
            return None

        results = []
        for lang, code in blocks:
            logger.info("执行代码块 (%d 字符)", len(code))
            r = self.code_executor.execute(code)
            if r["success"]:
                if r.get("done"):
                    results.append(f"(代码完成) 结果: {r.get('result', '')}")
                elif r["stdout"].strip():
                    results.append(f"输出:\n{r['stdout'].strip()[:2000]}")
                else:
                    results.append("(代码执行成功)")
            else:
                results.append(f"错误: {r['error'][:500]}")
            if r["stderr"]:
                results.append(f"stderr: {r['stderr'][:500]}")

        return "\n".join(results)

    # ── 引导机制 [H] ────────────────────────────────────

    def steer(self, message: str):
        """注入引导信息到下一轮工具结果，不中断对话。"""
        self._steer_message = message

    def _drain_steer(self) -> str | None:
        msg = self._steer_message
        self._steer_message = None
        return msg

    # ── 流式对话 [H+CC] ─────────────────────────────────

    def _stream_response(self, messages: List[Dict[str, Any]],
                         on_tool_call=None,
                         on_tool_batch=None,
                         on_usage=None) -> Generator[str, None, None]:
        """统一流式响应引擎——累积文本后批量执行工具调用，递归继续。

        on_tool_call: 可选，单工具回调 (tool_name, display_name, args_dict) -> None
        on_tool_batch: 可选，批次回调 ([(tool_name, display_name, emoji, args_dict), ...]) -> None
        on_usage: 可选，token 用量回调 (prompt_tokens, completion_tokens) -> None
        """
        if self.budget.exhausted:
            yield "\n（已达到最大对话轮次）"
            return

        # 上下文压缩检查 [CC]
        messages, was_compressed = self._maybe_compress(messages)
        if was_compressed:
            self.messages = messages
            yield "\n💡 上下文已自动压缩，保持对话流畅...\n"

        # 截断历史上下文
        llm_messages = self._get_llm_messages(messages)

        tools = self._get_tools()
        full_response = ""
        tool_calls = None

        try:
            for chunk in self.llm.chat_stream(llm_messages, tools=tools):
                if "_tool_calls" in chunk:
                    tool_calls = chunk["_tool_calls"]
                elif "_usage" in chunk:
                    if on_usage:
                        u = chunk["_usage"]
                        on_usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                elif chunk.get("content"):
                    full_response += chunk["content"]
                    yield chunk["content"]
        except Exception as e:
            logger.error("LLM 流式调用失败: %s", e)
            yield f"\n（模型调用出错: {e}）"
            return

        # [smolagents] 执行代码块
        if full_response and not tool_calls:
            code_result = self._handle_code_blocks(full_response)
            if code_result:
                yield f"\n\n📎 代码执行:\n{code_result}"

        if tool_calls:
            if not self.budget.consume():
                yield "\n（已达到最大对话轮次）"
                return

            self.messages.append({
                "role": "assistant",
                "content": full_response or "",
                "tool_calls": tool_calls,
            })
            self._log_msg("assistant", full_response)

            # 批量工具回调（防刷屏，先于单工具回调）
            if on_tool_batch:
                batch_info = []
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    entry = registry._tools.get(fn)
                    batch_info.append((
                        fn,
                        entry.display_name if entry else fn,
                        entry.emoji if entry else "🔧",
                        args,
                    ))
                on_tool_batch(batch_info)

            try:
                results = self.tool_executor.execute_batch(tool_calls)
            except Exception as e:
                logger.error("工具批量执行失败: %s", e)
                yield f"\n（工具执行出错: {e}）"
                return
            steer_msg = self._drain_steer()

            for i, tc in enumerate(tool_calls):
                fn = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                # 工具调用回调（UI 显示等）
                if on_tool_call:
                    entry = registry._tools.get(fn)
                    display_fn = entry.display_name if entry else fn
                    on_tool_call(fn, display_fn, args)

                result_content = results[i]["content"] if i < len(results) else '{"error": "执行失败"}'
                if steer_msg and i == len(tool_calls) - 1:
                    try:
                        data = json.loads(result_content)
                        data["_steer"] = steer_msg
                        result_content = json.dumps(data, ensure_ascii=False)
                    except Exception:
                        result_content += f"\n\n💡 {steer_msg}"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_content,
                })
                self._log_msg("tool", f"{fn}: {str(result_content)[:300]}")

            yield from self._stream_response(self.messages, on_tool_call=on_tool_call, on_tool_batch=on_tool_batch, on_usage=on_usage)
        else:
            # 最终文本响应 — 存入消息历史
            if full_response:
                self.messages.append({
                    "role": "assistant",
                    "content": full_response,
                })
                self._log_msg("assistant", full_response)

    def _get_llm_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        截断对话历史，避免 LLM 回顾旧消息导致重复回答。

        保留: system + 最近一轮完整对话（含工具调用链）。
        工具消息必须紧跟其 assistant(tool_calls) 父消息。
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]

        if len(other_msgs) <= 2:
            return system_msgs + list(other_msgs)

        # 从尾部保留：至少包含最近的 user + assistant(tool_calls) + tool 结果链
        keep = []
        seen_tool_calls = False
        for m in reversed(other_msgs):
            keep.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                seen_tool_calls = True
            if m.get("role") == "user" and (seen_tool_calls or len(keep) >= 2):
                break
        keep.reverse()
        return system_msgs + keep

    def chat(self, user_input: str) -> str:
        """非流式对话——返回完整响应字符串。"""
        if not self.messages:
            self.messages.append({"role": "system", "content": self.system_prompt})

        self.messages.append({"role": "user", "content": user_input})
        self._log_msg("user", user_input)
        self.budget.reset()

        while not self.budget.exhausted:
            # 上下文压缩检查 [CC]
            compressed, was_compressed = self._maybe_compress(self.messages)
            if was_compressed:
                self.messages = compressed

            # 截断历史上下文后发送给 LLM
            llm_messages = self._get_llm_messages(self.messages)
            try:
                response = self.llm.chat(llm_messages, tools=self._get_tools())
            except Exception as e:
                logger.error("LLM 调用失败: %s", e)
                return f"（模型调用出错: {e}）"

            if response.get("tool_calls"):
                tcs = response["tool_calls"]
                if not self.budget.consume():
                    break
                self.messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": tcs,
                })
                self._log_msg("assistant", response.get("content", ""))

                try:
                    results = self.tool_executor.execute_batch(tcs)
                except Exception as e:
                    logger.error("工具批量执行失败: %s", e)
                    return f"（工具执行出错: {e}）"
                steer_msg = self._drain_steer()

                for i, tc in enumerate(tcs):
                    result_content = results[i]["content"] if i < len(results) else '{"error": "执行失败"}'
                    if steer_msg and i == len(tcs) - 1:
                        try:
                            data = json.loads(result_content)
                            data["_steer"] = steer_msg
                            result_content = json.dumps(data, ensure_ascii=False)
                        except Exception:
                            result_content += f"\n\n💡 {steer_msg}"

                    self.messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": result_content,
                    })
                    fn = tc["function"]["name"]
                    self._log_msg("tool", f"{fn}: {str(result_content)[:300]}")
                continue

            final = response.get("content", "")
            self.messages.append({"role": "assistant", "content": final})
            self._log_msg("assistant", final)
            return final

        return "（已达到最大对话轮次）"

    def stream_chat(self, user_input: str) -> Generator[str, None, None]:
        """流式对话——逐 token 输出。"""
        if not self.messages:
            self.messages.append({"role": "system", "content": self.system_prompt})

        self.messages.append({"role": "user", "content": user_input})
        self._log_msg("user", user_input)
        self.budget.reset()

        yield from self._stream_response(self.messages)

    # ── 日志 & 记忆 ────────────────────────────────────

    def _log_msg(self, role: str, content: str):
        self.all_messages.append({
            "role": role,
            "content": str(content)[:2000],
            "time": time.time(),
        })
        if self.memory_enabled and hasattr(self, "memory") and self.memory._chroma_store:
            try:
                self.memory._chroma_store.log_message(self.session_id, role, str(content)[:2000])
            except Exception:
                pass

    def consolidate(self):
        """会话结束时的记忆整合。[H] + 会话保存 [原创]"""
        try:
            self._save_session()
        except Exception:
            pass

        if not self.all_messages or len(self.all_messages) < 2:
            return
        if self.memory_enabled and hasattr(self, "memory"):
            try:
                result = self.memory.consolidate_session(self.session_id, self.all_messages)
                if result.get("consolidated"):
                    logger.info("记忆已整合: %d 条事实", result.get("fact_count", 0))
            except KeyboardInterrupt:
                pass
            except Exception as e:
                logger.debug("记忆整合失败: %s", e)

    # ── 会话持久化 [原创] ─────────────────────────────────

    def _save_session(self):
        """保存当前会话。"""
        if not self.messages:
            return
        try:
            self.session_store.save(
                self.session_id,
                self.messages,
                meta={
                    "name": self._session_name or f"会话 {self.session_id[:8]}",
                    "model_display": self.config.get_model_display_name(),
                    "created_at": time.time(),
                },
            )
        except Exception as e:
            logger.warning("自动保存会话失败: %s", e)

    def _maybe_auto_save(self):
        """每 N 轮自动保存。"""
        self._turn_counter += 1
        if self._turn_counter % 10 == 0:
            try:
                self._save_session()
            except Exception:
                pass

    def restore_session(self, session_id: str) -> int:
        """恢复会话，返回消息数量。"""
        data = self.session_store.load(session_id)
        if not data:
            return 0
        self.messages = data.get("messages", [])
        self.session_id = session_id
        self._session_name = data.get("meta", {}).get("name", "")
        return len(self.messages)

    def _speak(self, text: str):
        if not self.auto_speak:
            return
        try:
            from ..voice.tts_providers import EdgeTTSProvider
            voice = self.voice_config.get("tts_voice", "zh-CN-female")
            EdgeTTSProvider(voice=voice).speak_and_play(text)
        except Exception:
            pass

    # ── CLI ────────────────────────────────────────────────

    def run_cli(self):
        from .cli import (
            print_banner, stream_assistant,
            print_tool_batch, reset_tool_tally, add_token_usage,
            print_goodbye, print_memory, print_help_bar,
            print_help_panel, print_config_panel, print_tools_panel,
            print_sessions_panel, print_info_panel,
            interactive_command_picker, interactive_session_picker,
            interactive_model_picker,
            BRONZE,
        )
        from rich.text import Text

        display_name = self.config.get_model_display_name()
        print_banner(display_name)
        print_help_bar()

        def stream_with_ui(messages):
            """流式输出 + UI 工具调用指示器——_stream_response 的薄包装。"""
            def on_tool_batch(batch_info):
                print_tool_batch(batch_info)
            def on_usage(prompt_tokens, completion_tokens):
                add_token_usage(prompt_tokens, completion_tokens)
            yield from self._stream_response(messages, on_tool_batch=on_tool_batch, on_usage=on_usage)

        # 输入历史
        _history = []

        def _get_input():
            """读取用户输入，支持剪头键和简单编辑。"""
            from rich.console import Console
            c = Console()
            c.print()
            c.print(Text("  ◇  你 ▸ ", style=f"bold {BRONZE}"), end="")
            try:
                return input().strip()
            except (EOFError, KeyboardInterrupt):
                return None

        def _run_bash(cmd: str):
            """直接执行 bash 命令。"""
            from .tool_executor import ToolExecutor
            executor = ToolExecutor()
            result = executor.execute("bash", {"command": cmd})
            output = result.get("output", "") or result.get("error", "")
            print_info_panel("! 执行", output[:2000] or "(无输出)")

        def _insert_file(path: str):
            """读取文件内容拼入消息。"""
            from pathlib import Path
            fp = Path(path).expanduser().resolve()
            # 敏感路径防护
            _SENSITIVE_PATHS = [
                Path.home() / ".ssh",
                Path.home() / ".aws",
                Path.home() / ".gcp",
                Path.home() / ".config" / "gh",
                Path.home() / ".gitconfig",
                Path.home() / ".netrc",
                Path("/etc") / "passwd",
                Path("/etc") / "shadow",
                Path("/etc") / "hosts",
                Path("/var") / "root",
            ]
            if any(fp == sp or fp.is_relative_to(sp) for sp in _SENSITIVE_PATHS):
                print_info_panel("文件", f"拒绝读取敏感路径: {path}")
                return None
            if not fp.exists():
                print_info_panel("文件", f"不存在: {path}")
                return None
            try:
                content = fp.read_text()
                if len(content) > 10000:
                    content = content[:10000] + "\n... (截断)"
                print_info_panel("文件", f"已读入: {fp.name} ({len(content)} 字符)")
                return content
            except Exception as e:
                print_info_panel("文件", f"读取失败: {e}")
                return None

        try:
            while True:
                user_input = _get_input()
                if user_input is None:
                    print()
                    break

                if not user_input:
                    continue

                # 记录历史
                _history.append(user_input)

                # ── ! 前缀：直接执行 bash ──
                if user_input.startswith("!"):
                    _run_bash(user_input[1:].strip())
                    continue

                # ── @ 前缀：引用文件 ──
                if user_input.startswith("@"):
                    content = _insert_file(user_input[1:].strip())
                    if content is None:
                        continue
                    user_input = f"参考以下文件内容：\n\n{content}"

                # ── / 命令 ──
                elif user_input == "/quit":
                    break
                elif user_input in ("/help", "/"):
                    cmd = interactive_command_picker()
                    if cmd:
                        user_input = cmd
                    else:
                        continue
                elif user_input == "/model":
                    result = interactive_model_picker(
                        self.config.provider_config,
                        self.config.model_config.get("provider", ""),
                        self.config.default_model,
                    )
                    if result:
                        pname, mkey = result
                        self.llm = LLMClient(model_key=mkey)
                        display = self.llm.model
                        print_info_panel("模型", f"已切换至: {display}  (@{pname})")
                    continue
                elif user_input == "/config":
                    try:
                        mc = self.config.model_config or {}
                        ac = self.config.agent_config or {}
                        memc = self.config.memory_config or {}
                        vc = self.config.voice_config or {}
                        cc = self.config.computer_config or {}
                        print_config_panel({
                            "model_display": self.config.get_model_display_name(),
                            "provider": mc.get("provider", "?"),
                            "base_url": mc.get("base_url", "?"),
                            "max_turns": ac.get("max_turns", "?"),
                            "timeout": ac.get("timeout", 1800),
                            "memory_enabled": memc.get("enabled", False),
                            "auto_speak": vc.get("auto_speak", False),
                            "computer_use": cc.get("enabled", False),
                            "log_level": self.config._data.get("logging", {}).get("level", "?"),
                        })
                    except Exception:
                        print_info_panel("配置", "无法读取配置")
                    continue
                elif user_input == "/clear":
                    self.messages = []
                    self.budget.reset()
                    print_info_panel("会话", "已重置～")
                    continue
                elif user_input == "/tools":
                    tool_list = []
                    for name, entry in registry._tools.items():
                        tool_list.append({
                            "name": name,
                            "display_name": entry.display_name,
                            "emoji": entry.emoji,
                            "toolset": entry.toolset,
                            "description": entry.description,
                        })
                    print_tools_panel(tool_list)
                    continue
                elif user_input == "/memory":
                    fs = self.memory._file_store if hasattr(self, "memory") and self.memory else None
                    cs = self.memory._chroma_store if hasattr(self, "memory") and self.memory else None
                    print_memory(
                        fs.user_entries if fs else [],
                        fs.memory_entries if fs else [],
                        cs.search_facts(limit=5) if cs else [],
                    )
                    continue
                elif user_input == "/voice":
                    self.auto_speak = not self.auto_speak
                    print_info_panel("语音", f"播报: {'开' if self.auto_speak else '关'}")
                    continue
                elif user_input == "/compress":
                    stats = self.compressor.stats
                    ratio = self.compressor.get_usage_ratio(self.messages)
                    print_info_panel("上下文压缩",
                        f"压缩次数: {stats['compression_count']}\n"
                        f"裁剪字符: {stats['pruned_chars']:,}\n"
                        f"摘要轮次: {stats['summarized_turns']}\n"
                        f"当前使用率: {ratio:.1%}  (阈值: 50%)")
                    continue
                elif user_input == "/delegate":
                    beats = self.sub_agent_pool.get_heartbeats()
                    if beats:
                        lines = "\n".join(
                            f"[{b['agent_id']}] {b['status']} (轮次:{b['turns']}, 深度:{b['depth']})"
                            for b in beats[-20:])
                        print_info_panel("子代理", lines)
                    else:
                        print_info_panel("子代理", "无活跃子代理")
                    continue
                elif user_input == "/sessions":
                    sessions = self.session_store.list_sessions()
                    sid = interactive_session_picker(sessions)
                    if sid:
                        count = self.restore_session(sid)
                        if count:
                            print_info_panel("会话", f"已恢复: {count} 条消息")
                        else:
                            print_info_panel("会话", f"恢复失败: {sid[:8]}")
                    continue
                elif user_input.startswith("/load "):
                    sid = user_input[6:].strip()
                    count = self.restore_session(sid)
                    if count:
                        print_info_panel("会话", f"已恢复: {count} 条消息")
                    else:
                        print_info_panel("会话", f"未找到: {sid}")
                    continue
                elif user_input == "/save":
                    self._session_name = f"手动保存 {time.strftime('%H:%M')}"
                    self._save_session()
                    print_info_panel("会话", f"已保存 [{self.session_id[:8]}]")
                    continue
                elif user_input == "/bg":
                    from ..core.background_task import get_bg_manager
                    mgr = get_bg_manager()
                    tasks = mgr.list_tasks()
                    if tasks:
                        lines = "\n".join(
                            f"[{t['task_id'][:8]}] {t['status']} "
                            f"exit={t.get('exit_code', '?')} "
                            f"({t.get('duration_ms', 0)}ms) {t['command'][:60]}"
                            for t in tasks)
                        print_info_panel("后台任务", lines)
                    else:
                        print_info_panel("后台任务", "无后台任务")
                    continue

                # ── 普通消息 → AI ──
                reset_tool_tally()
                if not self.messages:
                    self.messages.append({"role": "system", "content": self.system_prompt})
                self.messages.append({"role": "user", "content": user_input})
                self._log_msg("user", user_input)

                if not self._session_name:
                    self._session_name = user_input[:50]

                self.budget.reset()
                self._maybe_auto_save()
                try:
                    full = stream_assistant(stream_with_ui(self.messages))
                except Exception as e:
                    logger.error("对话出错: %s", e)
                    print_error(f"对话出错: {e}")
                    continue

                if self.auto_speak and full.strip():
                    self._speak(full)

        finally:
            try:
                self.consolidate()
            except KeyboardInterrupt:
                pass
            # 统计记忆数量
            mem_count = 0
            if hasattr(self, "memory") and self.memory and self.memory._file_store:
                mem_count = len(self.memory._file_store.memory_entries or [])
            print_goodbye(
                session_name=self._session_name or "",
                turn_count=self.budget.consumed,
                memory_count=mem_count,
            )
