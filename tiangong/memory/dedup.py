"""记忆去重 — MD5 哈希 + 批量去重。[借鉴mem0]

借鉴 mem0/memory/main.py 的 hash dedup 模式:
- 添加记忆时 MD5 哈希
- 与现有记忆哈希池比对
- 批量插入时内部去重
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def hash_content(content: str) -> str:
    """计算记忆内容的 MD5 哈希。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def hash_content_normalized(content: str) -> str:
    """归一化后计算哈希——忽略空白差异。"""
    normalized = " ".join(content.split()).lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


class DedupManager:
    """记忆去重管理器。"""

    def __init__(self):
        self._hashes: Set[str] = set()       # 精确哈希
        self._norm_hashes: Set[str] = set()  # 归一化哈希
        self._content_keys: Set[str] = set()  # 前100字

    def load_existing(self, documents: List[str]):
        """加载现有记忆建立哈希池。"""
        for doc in documents:
            self.add_hash(doc)
        logger.debug("去重池已初始化: %d 条记忆", len(self._hashes))

    def add_hash(self, content: str):
        """将内容加入哈希池（不实际存储）。"""
        self._hashes.add(hash_content(content))
        self._norm_hashes.add(hash_content_normalized(content))
        self._content_keys.add(content[:100].strip().lower())

    def remove_hash(self, content: str):
        """从哈希池移除。"""
        h = hash_content(content)
        nh = hash_content_normalized(content)
        ck = content[:100].strip().lower()
        self._hashes.discard(h)
        self._norm_hashes.discard(nh)
        self._content_keys.discard(ck)

    def is_duplicate(self, content: str) -> Tuple[bool, str]:
        """检查是否为重复记忆。

        返回: (是否重复, 原因)
        """
        # 1. 精确匹配
        h = hash_content(content)
        if h in self._hashes:
            return True, "exact_hash"

        # 2. 归一化匹配（忽略空白和大小写）
        nh = hash_content_normalized(content)
        if nh in self._norm_hashes:
            return True, "normalized_hash"

        # 3. 前100字匹配（近似重复）
        ck = content[:100].strip().lower()
        if ck and ck in self._content_keys:
            return True, "prefix_match"

        return False, ""

    def filter_duplicates(self, items: List[dict],
                          content_key: str = "content") -> List[dict]:
        """过滤重复项，返回新项列表并自动加入哈希池。"""
        new_items = []
        for item in items:
            content = item.get(content_key, "")
            if not content:
                new_items.append(item)
                continue

            is_dup, reason = self.is_duplicate(content)
            if is_dup:
                logger.debug("跳过重复记忆: %s (%s)", content[:50], reason)
                continue

            self.add_hash(content)
            new_items.append(item)

        return new_items

    def get_stats(self) -> dict:
        return {
            "exact_hashes": len(self._hashes),
            "normalized_hashes": len(self._norm_hashes),
            "prefix_keys": len(self._content_keys),
        }


# 模块级单例
_dedup_manager: Optional[DedupManager] = None


def get_dedup() -> DedupManager:
    global _dedup_manager
    if _dedup_manager is None:
        _dedup_manager = DedupManager()
    return _dedup_manager
