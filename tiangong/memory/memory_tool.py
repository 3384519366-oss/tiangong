"""Persistent memory tool — file-based + ChromaDB semantic search.

Entry delimiter: § (section sign). Atomic file writes via tempfile + os.replace.
Now integrated with ChromaDB store for semantic memory.
"""

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from tiangong.core.config import Config
from tiangong.core.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"


def get_memory_dir() -> Path:
    return Config.get().get_memory_dir()


class FileMemoryStore:
    """File-based memory (MEMORY.md/USER.md) — backward compatible, fast access."""

    def __init__(self, memory_char_limit: int = 5000, user_char_limit: int = 3000):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit

    def load(self):
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)
        self.memory_entries = self._read(mem_dir / "MEMORY.md")
        self.user_entries = self._read(mem_dir / "USER.md")
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _path_for(self, target: str) -> Path:
        if target == "user":
            return get_memory_dir() / "USER.md"
        return get_memory_dir() / "MEMORY.md"

    def _entries_for(self, target: str) -> List[str]:
        return self.user_entries if target == "user" else self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def _char_limit(self, target: str) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def _save(self, target: str):
        entries = self._entries_for(target)
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        path = self._path_for(target)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".mem_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise RuntimeError(f"Failed to write memory: {e}")

    @staticmethod
    def _read(path: Path) -> List[str]:
        if not path.exists():
            return []
        try:
            raw = path.read_text("utf-8")
        except OSError:
            return []
        if not raw.strip():
            return []
        return [e for e in (e.strip() for e in raw.split(ENTRY_DELIMITER)) if e]

    def add(self, target: str, content: str) -> dict:
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}
        with self._file_lock(self._path_for(target)):
            entries = self._entries_for(target)
            if content in entries:
                return self._success(target, "Already exists.")
            limit = self._char_limit(target)
            new_total = len(ENTRY_DELIMITER.join(entries + [content]))
            if new_total > limit:
                return {
                    "success": False,
                    "error": f"Would exceed {limit:,} char limit. Remove old entries first.",
                }
            entries.append(content)
            self._set_entries(target, entries)
            self._save(target)
        return self._success(target, "Added.")

    def remove(self, target: str, old_text: str) -> dict:
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text required."}
        with self._file_lock(self._path_for(target)):
            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}
            if len(matches) > 1:
                unique = set(e for _, e in matches)
                if len(unique) > 1:
                    return {"success": False, "error": f"Multiple matches for '{old_text}'. Be more specific."}
            entries.pop(matches[0][0])
            self._set_entries(target, entries)
            self._save(target)
        return self._success(target, "Removed.")

    def _success(self, target: str, msg: str) -> dict:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        return {
            "success": True, "target": target, "message": msg,
            "entries": entries,
            "usage": f"{current:,}/{limit:,} chars ({len(entries)} entries)",
        }


# Module-level stores
_file_store: Optional[FileMemoryStore] = None
_chroma_store: Optional[object] = None  # MemoryStore from store.py


def init_memory(memory_char_limit: int = 5000, user_char_limit: int = 3000):
    global _file_store, _chroma_store
    config = Config.get()
    mc = config.memory_config
    _file_store = FileMemoryStore(
        memory_char_limit=mc.get("memory_char_limit", memory_char_limit),
        user_char_limit=mc.get("user_char_limit", user_char_limit),
    )
    _file_store.load()
    # Lazy-load ChromaDB store
    try:
        from .store import get_store
        _chroma_store = get_store()
        logger.info("ChromaDB memory store initialized")
    except Exception as e:
        logger.warning("ChromaDB store not available: %s", e)


def get_file_store() -> Optional[FileMemoryStore]:
    return _file_store


def get_chroma_store():
    return _chroma_store


def memory_tool_handler(args: dict, **kwargs) -> str:
    store = _file_store
    if not store:
        return tool_error("记忆系统不可用。")
    action = args.get("action", "")
    target = args.get("target", "memory")
    content = args.get("content")
    old_text = args.get("old_text")
    query = args.get("query")
    mem_type = args.get("type", "project")  # [CC] 类型化记忆
    description = args.get("description", "")  # [CC] 记忆描述

    if target not in ("memory", "user", "semantic"):
        return tool_error("目标必须是 'memory'、'user' 或 'semantic'。")

    if action == "add":
        if not content:
            return tool_error("添加记忆需要提供 content。")

        # 使用 MemoryManager 添加类型化记忆 [CC]
        try:
            from .memory_manager import get_memory_manager
            mm = get_memory_manager()
            if mem_type and target != "semantic":
                result = mm.add_typed(content, mem_type=mem_type, target=target,
                                     description=description)
            else:
                result = store.add(target, content)
                if result.get("success") and _chroma_store:
                    try:
                        _chroma_store.add_semantic(content, metadata={"category": target})
                        _chroma_store.add_fact(content, category=target)
                    except Exception as e:
                        logger.warning("写入 ChromaDB 失败: %s", e)
        except Exception:
            # 回退到直接文件写入
            result = store.add(target, content)
            if result.get("success") and _chroma_store:
                try:
                    _chroma_store.add_semantic(content, metadata={"category": target})
                    _chroma_store.add_fact(content, category=target)
                except Exception as e:
                    logger.warning("写入 ChromaDB 失败: %s", e)

        return json.dumps(result, ensure_ascii=False)

    elif action == "remove":
        if not old_text:
            return tool_error("删除记忆需要提供 old_text。")
        result = store.remove(target, old_text)
        return json.dumps(result, ensure_ascii=False)

    elif action == "search":
        if not query:
            return tool_error("搜索需要提供 query。")
        if not _chroma_store:
            return tool_error("语义搜索不可用（ChromaDB 未加载）。")
        results = _chroma_store.hybrid_search(query, limit=5)
        return json.dumps(results, ensure_ascii=False, default=str)

    elif action == "budget":
        # [CC] 上下文预算查询
        try:
            from .memory_manager import get_memory_manager
            mm = get_memory_manager()
            budget = mm.get_context_budget()
            return json.dumps(budget, ensure_ascii=False)
        except Exception as e:
            return tool_error(f"预算查询失败: {e}")

    else:
        return tool_error(f"未知操作: {action}。可用操作: 添加(add)、删除(remove)、搜索(search)、预算(budget)。")


MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "管理跨会话的持久记忆。支持类型化存储和安全扫描。"
        "目标: 'memory'（环境事实、项目、工具），"
        "'user'（用户偏好、名字、习惯），"
        "'semantic'（按语义搜索过去的记忆）。"
        "操作: add(添加)、remove(删除)、search(搜索)、budget(上下文预算)。"
        "类型: user(用户身份/偏好)、feedback(行为反馈)、"
        "project(项目上下文)、reference(外部资源指针)。"
        "当用户分享偏好或事实时，主动使用 'add'。"
        "使用 'search' 查找相关的历史记忆。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "remove", "search", "budget"],
                "description": "操作: add(添加), remove(删除), search(搜索), budget(预算查询)"
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "semantic"],
                "description": "记忆目标: memory(环境), user(用户), semantic(语义搜索)"
            },
            "content": {
                "type": "string",
                "description": "记忆内容（add 操作必填）。"
            },
            "old_text": {
                "type": "string",
                "description": "要删除的记忆片段关键词（remove 操作必填）。"
            },
            "query": {
                "type": "string",
                "description": "搜索关键词（search 操作必填）。"
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "记忆类型 [CC]: user(用户), feedback(反馈), project(项目), reference(参考)"
            },
            "description": {
                "type": "string",
                "description": "记忆的单行描述，用于相关性判断。"
            },
        },
        "required": ["action", "target"],
    },
}

registry.register(
    name="memory",
    toolset="记忆",
    schema=MEMORY_SCHEMA,
    handler=memory_tool_handler,
    description="跨会话持久记忆，支持语义搜索",
    emoji="🧠",
    display_name="记忆",
)
