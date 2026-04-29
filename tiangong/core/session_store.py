"""会话持久化 — 保存/恢复/列表会话。[原创]

每个会话保存为独立 JSON 文件:
- 自动保存（每 N 轮触发）
- 会话列表（名称、时间、轮次数）
- 恢复会话（加载消息历史 + 系统提示）
- 最多保留 50 个会话
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 50
_AUTO_SAVE_INTERVAL = 10  # 每 10 轮自动保存


class SessionMeta:
    """会话元数据。"""
    __slots__ = ("session_id", "name", "created_at", "updated_at",
                 "message_count", "model_display", "first_message")

    def __init__(self):
        self.session_id: str = ""
        self.name: str = ""
        self.created_at: float = 0
        self.updated_at: float = 0
        self.message_count: int = 0
        self.model_display: str = ""
        self.first_message: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "model_display": self.model_display,
            "first_message": self.first_message[:100],
        }


class SessionStore:
    """会话持久化存储。"""

    def __init__(self):
        config = Config.get()
        data_dir = config.get_memory_dir()
        self._sessions_dir = data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._sessions_dir / "index.json"
        self._index: Dict[str, dict] = {}
        self._load_index()

    def _load_index(self):
        """加载会话索引。"""
        if self._index_path.exists():
            try:
                self._index = json.loads(self._index_path.read_text())
            except Exception:
                self._index = {}

    def _save_index(self):
        """保存会话索引。"""
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            logger.warning("保存会话索引失败: %s", e)

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.json"

    def save(self, session_id: str, messages: List[Dict[str, Any]],
             meta: dict = None) -> bool:
        """保存会话。

        messages: 完整消息列表（包含 system prompt）
        meta: {name, model_display, ...}
        """
        try:
            meta = meta or {}
            now = time.time()

            # 序列化消息（过滤不可序列化的对象）
            clean_messages = self._sanitize_messages(messages)

            session_data = {
                "session_id": session_id,
                "messages": clean_messages,
                "message_count": len(clean_messages),
                "saved_at": now,
                "meta": meta,
            }

            self._session_path(session_id).write_text(
                json.dumps(session_data, ensure_ascii=False, indent=2)
            )

            # 更新索引
            first_msg = ""
            for m in clean_messages:
                if m.get("role") == "user":
                    first_msg = str(m.get("content", ""))[:100]
                    break

            self._index[session_id] = {
                "name": meta.get("name", f"会话 {session_id[:8]}"),
                "created_at": meta.get("created_at", now),
                "updated_at": now,
                "message_count": len(clean_messages),
                "model_display": meta.get("model_display", ""),
                "first_message": first_msg,
            }
            self._save_index()
            self._prune_old()

            logger.info("会话已保存: %s (%d 条消息)", session_id, len(clean_messages))
            return True

        except Exception as e:
            logger.warning("保存会话失败: %s", e)
            return False

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """加载会话。"""
        path = self._session_path(session_id)
        if not path.exists():
            return None

        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("加载会话失败: %s", e)
            return None

    def load_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """只加载消息列表。"""
        data = self.load(session_id)
        if data:
            return data.get("messages", [])
        return []

    def list_sessions(self) -> List[dict]:
        """列出所有会话。"""
        sessions = []
        for sid, info in sorted(
            self._index.items(),
            key=lambda x: x[1].get("updated_at", 0),
            reverse=True,
        ):
            sessions.append({
                "session_id": sid,
                **info,
            })
        return sessions

    def branch(self, session_id: str, at_index: int = -1) -> Optional[str]:
        """从现有会话分叉，创建新会话。[原创]

        session_id: 源会话 ID
        at_index: 从第几条消息处截断（默认 -1 表示复制全部消息）
        返回: 新会话 ID，失败返回 None
        """
        import uuid
        data = self.load(session_id)
        if not data:
            return None

        messages = data.get("messages", [])
        meta = data.get("meta", {})

        if 0 <= at_index < len(messages):
            branched_messages = messages[:at_index + 1]
        else:
            branched_messages = list(messages)

        new_id = uuid.uuid4().hex[:12]
        new_meta = dict(meta)
        new_meta["name"] = f"{meta.get('name', '会话')} (分叉)"
        new_meta["parent_session"] = session_id
        new_meta["branch_point"] = at_index

        ok = self.save(new_id, branched_messages, meta=new_meta)
        return new_id if ok else None

    def delete(self, session_id: str) -> bool:
        """删除会话。"""
        path = self._session_path(session_id)
        deleted = False
        if path.exists():
            try:
                os.unlink(path)
                deleted = True
            except OSError:
                pass

        if session_id in self._index:
            del self._index[session_id]
            self._save_index()

        return deleted

    def _prune_old(self):
        """删除超过最大数量的旧会话。"""
        if len(self._index) <= _MAX_SESSIONS:
            return

        # 按更新时间排序，删除最旧的
        sorted_ids = sorted(
            self._index.items(),
            key=lambda x: x[1].get("updated_at", 0),
        )
        to_delete = sorted_ids[:len(sorted_ids) - _MAX_SESSIONS]

        for sid, _ in to_delete:
            self.delete(sid)
            logger.debug("已清理旧会话: %s", sid)

    @staticmethod
    def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """清理消息中的不可序列化内容。"""
        clean = []
        for msg in messages:
            m = {}
            for k, v in msg.items():
                if k == "tool_calls":
                    m[k] = v  # 保留工具调用
                elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                    # 截断过长的内容
                    if isinstance(v, str) and len(v) > 10000:
                        m[k] = v[:10000] + "... (已截断)"
                    else:
                        m[k] = v
            if m:
                clean.append(m)
        return clean


# 模块级单例
_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
