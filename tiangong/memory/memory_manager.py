"""MemoryManager — 统一记忆管理，多 Provider 架构。[H+CC融合]

借鉴 Hermes: Provider 架构 + 生命周期钩子
借鉴 Claude Code: 类型化记忆 + 上下文预算追踪 + 安全扫描

Provider 层级:
- FileMemoryProvider: MEMORY.md/USER.md 文件存储（快速访问，上下文注入）
- ChromaDBProvider: 语义向量搜索（相似记忆检索）
- SQLiteProvider: 情节记忆 + 事实存储 + 会话日志
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tiangong.core.config import Config
from .memory_tool import get_file_store, FileMemoryStore
from .store import get_store, MemoryStore
from .security import MemorySecurityScanner

logger = logging.getLogger(__name__)

# 记忆类型 [CC]
MEMORY_TYPES = ("user", "feedback", "project", "reference")

# 上下文预算 [CC]
CONTEXT_BUDGET_LINES = 200


class MemoryManager:
    """统一记忆管理器 — 协调所有 Provider。"""

    def __init__(self):
        config = Config.get()
        mc = config.memory_config
        self.enabled = mc.get("enabled", True)
        self.memory_char_limit = mc.get("memory_char_limit", 5000)
        self.user_char_limit = mc.get("user_char_limit", 3000)

        self._file_store: Optional[FileMemoryStore] = None
        self._chroma_store: Optional[MemoryStore] = None
        self._scanner = MemorySecurityScanner()

        if self.enabled:
            self._init_providers()

    def _init_providers(self):
        """初始化所有记忆 Provider。[H]"""
        # File provider — 需要先调用 init_memory 加载 MEMORY.md/USER.md
        try:
            from .memory_tool import init_memory
            init_memory(
                memory_char_limit=self.memory_char_limit,
                user_char_limit=self.user_char_limit,
            )
            self._file_store = get_file_store()
            if self._file_store:
                logger.info("文件记忆 Provider 已就绪 (%d条记忆, %d条用户信息)",
                           len(self._file_store.memory_entries or []),
                           len(self._file_store.user_entries or []))
        except Exception as e:
            logger.warning("文件记忆 Provider 初始化失败: %s", e)

        # ChromaDB + SQLite provider
        try:
            self._chroma_store = get_store()
            logger.info("ChromaDB+SQLite Provider 已就绪")
        except Exception as e:
            logger.warning("ChromaDB Provider 初始化失败: %s", e)

    # ── 类型化记忆 [CC] ─────────────────────────────────────

    @staticmethod
    def _format_memory_entry(content: str, mem_type: str = "project",
                             description: str = "", metadata: dict = None) -> str:
        """格式化记忆条目为 YAML frontmatter 格式。[CC]"""
        meta = metadata or {}
        lines = ["---"]
        if description:
            lines.append(f"description: {description}")
        lines.append(f"type: {mem_type}")
        for k, v in meta.items():
            if isinstance(v, str):
                lines.append(f"{k}: {v}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
        lines.append(content)
        return "\n".join(lines)

    def add_typed(self, content: str, mem_type: str = "project",
                  target: str = "memory", description: str = "",
                  metadata: dict = None) -> dict:
        """添加类型化记忆。[CC]

        mem_type: user (用户身份/偏好), feedback (行为反馈),
                  project (项目上下文), reference (外部资源指针)
        """
        if mem_type not in MEMORY_TYPES:
            return {"success": False, "error": f"记忆类型必须是: {', '.join(MEMORY_TYPES)}"}

        # 安全扫描 [CC]
        safe, warnings = self._scanner.scan(content)
        if not safe:
            return {"success": False, "error": f"安全扫描未通过: {'; '.join(warnings)}"}

        content = self._scanner.sanitize(content)

        # 格式化并存储到文件 provider
        formatted = self._format_memory_entry(content, mem_type, description, metadata)
        result = {"success": True, "type": mem_type, "stored_in": []}

        if self._file_store:
            file_target = "user" if mem_type == "user" else "memory"
            r = self._file_store.add(file_target, formatted)
            if r.get("success"):
                result["stored_in"].append("file")

        # 同步到 ChromaDB + SQLite [H]
        if self._chroma_store:
            try:
                self._chroma_store.add_semantic(
                    content,
                    metadata={"category": mem_type, "description": description,
                             **(metadata or {})},
                )
                self._chroma_store.add_fact(content, category=mem_type)
                result["stored_in"].append("chromadb")
                result["stored_in"].append("sqlite")
            except Exception as e:
                logger.warning("ChromaDB 同步失败: %s", e)

        return result

    # ── 移除记忆 ────────────────────────────────────────────

    def remove(self, target: str, old_text: str) -> dict:
        if self._file_store:
            return self._file_store.remove(target, old_text)
        return {"success": False, "error": "文件记忆 Provider 不可用。"}

    # ── 搜索记忆 [H] ────────────────────────────────────────

    def search(self, query: str, limit: int = 5) -> dict:
        """语义搜索 + 关键词搜索。[H]"""
        if not self._chroma_store:
            return {"error": "语义搜索不可用（ChromaDB 未加载）。"}
        return self._chroma_store.hybrid_search(query, limit=limit)

    # ── 上下文预算追踪 [CC] ─────────────────────────────────

    def get_context_budget(self) -> dict:
        """获取当前上下文预算使用情况。[CC]"""
        if not self._file_store:
            return {"error": "不可用"}

        mem_chars = self._file_store._char_count("memory")
        user_chars = self._file_store._char_count("user")
        mem_entries = len(self._file_store.memory_entries)
        user_entries = len(self._file_store.user_entries)

        return {
            "memory": {
                "chars": mem_chars,
                "limit": self._file_store.memory_char_limit,
                "entries": mem_entries,
                "usage_pct": round(mem_chars / self._file_store.memory_char_limit * 100, 1) if self._file_store.memory_char_limit else 0,
            },
            "user": {
                "chars": user_chars,
                "limit": self._file_store.user_char_limit,
                "entries": user_entries,
                "usage_pct": round(user_chars / self._file_store.user_char_limit * 100, 1) if self._file_store.user_char_limit else 0,
            },
        }

    # ── 系统提示上下文 [H] ──────────────────────────────────

    def get_system_prompt_context(self, query: str = "", max_chars: int = 3000) -> str:
        """获取记忆上下文，用于注入 system prompt。[H]"""
        parts = []

        # 文件记忆（快速访问）
        if self._file_store:
            if self._file_store.user_entries:
                parts.append("═══ 用户信息 ═══\n" + "\n§\n".join(self._file_store.user_entries))
            if self._file_store.memory_entries:
                parts.append("═══ 记忆库 ═══\n" + "\n§\n".join(self._file_store.memory_entries))

        # 语义检索
        if self._chroma_store and query:
            try:
                ctx = self._chroma_store.get_context_for_prompt(query, max_chars)
                if ctx:
                    parts.append(ctx)
            except Exception:
                pass

        combined = "\n\n".join(parts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n... (已截断)"
        return combined

    # ── 会话整合 [H] ────────────────────────────────────────

    def consolidate_session(self, session_id: str, messages: List[Dict[str, Any]]) -> dict:
        """会话结束后整合记忆。[H]"""
        try:
            from .consolidator import consolidate_session
            return consolidate_session(session_id, messages)
        except Exception as e:
            logger.warning("记忆整合失败: %s", e)
            return {"consolidated": False, "error": str(e)}


# 模块级单例
_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
