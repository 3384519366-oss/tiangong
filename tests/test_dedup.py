"""测试记忆去重: 3 层哈希去重。[借鉴mem0]"""

import pytest
from tiangong.memory.dedup import (
    hash_content,
    hash_content_normalized,
    DedupManager,
    get_dedup,
)


class TestHashing:
    def test_hash_content_deterministic(self):
        a = hash_content("hello world")
        b = hash_content("hello world")
        assert a == b
        assert len(a) == 32  # MD5 hex

    def test_different_content_different_hash(self):
        a = hash_content("hello")
        b = hash_content("world")
        assert a != b

    def test_normalized_ignores_whitespace(self):
        a = hash_content_normalized("hello  world")
        b = hash_content_normalized("hello world")
        assert a == b

    def test_normalized_ignores_case(self):
        a = hash_content_normalized("Hello World")
        b = hash_content_normalized("hello world")
        assert a == b


class TestDedupManager:
    def test_new_manager_empty(self):
        dm = DedupManager()
        assert dm.get_stats()["exact_hashes"] == 0

    def test_load_existing(self):
        dm = DedupManager()
        dm.load_existing(["记忆一", "记忆二", "记忆三"])
        assert dm.get_stats()["exact_hashes"] == 3

    def test_exact_duplicate(self):
        dm = DedupManager()
        dm.add_hash("原始记忆")
        is_dup, reason = dm.is_duplicate("原始记忆")
        assert is_dup
        assert reason == "exact_hash"

    def test_whitespace_duplicate(self):
        dm = DedupManager()
        dm.add_hash("hello world")
        is_dup, reason = dm.is_duplicate("hello  world")
        assert is_dup
        assert reason == "normalized_hash"

    def test_prefix_duplicate(self):
        dm = DedupManager()
        content = "A" * 200  # 前100字作为 key
        dm.add_hash(content)
        is_dup, reason = dm.is_duplicate(content)  # exact match first
        assert is_dup

    def test_not_duplicate(self):
        dm = DedupManager()
        dm.add_hash("完全不同的记忆内容 A")
        is_dup, _ = dm.is_duplicate("完全不同的记忆内容 B")
        assert not is_dup

    def test_filter_duplicates(self):
        dm = DedupManager()
        dm.add_hash("记忆一")
        items = [
            {"content": "记忆一"},  # 重复
            {"content": "记忆二"},  # 新
        ]
        filtered = dm.filter_duplicates(items)
        assert len(filtered) == 1
        assert filtered[0]["content"] == "记忆二"

    def test_remove_hash(self):
        dm = DedupManager()
        dm.add_hash("测试记忆")
        assert dm.get_stats()["exact_hashes"] == 1
        dm.remove_hash("测试记忆")
        assert dm.get_stats()["exact_hashes"] == 0

    def test_empty_content_in_filter(self):
        dm = DedupManager()
        items = [{"content": ""}, {"content": "有效记忆"}]
        filtered = dm.filter_duplicates(items)
        assert len(filtered) == 2  # 空内容不参与去重


def test_get_dedup_singleton():
    a = get_dedup()
    b = get_dedup()
    assert a is b
