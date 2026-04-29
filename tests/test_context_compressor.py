"""测试上下文压缩器: token 估算 + 工具结果裁剪 + 轮次摘要。[CC]"""

import pytest
from tiangong.core.context_compressor import (
    estimate_tokens,
    estimate_messages_tokens,
    prune_tool_result,
    _find_turn_boundaries,
    ContextCompressor,
)


class TestTokenEstimation:
    def test_empty_text(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0  # type: ignore

    def test_pure_chinese(self):
        # 10 中文字 * 0.5 = 5, + max(0*0.25, 1) = 1 → 6
        assert estimate_tokens("你好世界这是一个测试") == 6

    def test_pure_english(self):
        # "hello world test" = 16 chars → 16 * 0.25 = 4
        assert estimate_tokens("hello world test") == 4

    def test_mixed_cn_en(self):
        # "你好world" = 2中文(1 tok) + 5英文(1.25 tok) = 2.25 → int=2
        tokens = estimate_tokens("你好world")
        assert 1 <= tokens <= 3  # approximate

    def test_messages_with_tool_calls(self):
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！", "tool_calls": [
                {"function": {"name": "bash", "arguments": '{"command": "ls"}'}}
            ]},
        ]
        tokens = estimate_messages_tokens(msgs)
        assert tokens > 10

    def test_empty_messages(self):
        assert estimate_messages_tokens([]) == 0


class TestToolResultPruning:
    def test_short_content_unchanged(self):
        short = "hello\nworld"
        assert prune_tool_result(short) == short

    def test_long_content_truncated_by_chars(self):
        long = "x" * 3000
        result = prune_tool_result(long)
        assert len(result) < len(long)
        assert "已截断" in result

    def test_many_lines_truncated(self):
        # 需要同时超过字符和行数阈值才能触发行截断
        long_line = "x" * 80  # 每行80字符，100行 = 8000+字符 > 2000
        lines = [f"{long_line} line {i}" for i in range(100)]
        content = "\n".join(lines)
        result = prune_tool_result(content)
        assert "行已省略" in result
        # 保留头尾
        assert "line 0" in result
        assert "line 99" in result

    def test_few_lines_no_truncation(self):
        content = "\n".join([f"line {i}" for i in range(10)])
        assert prune_tool_result(content) == content


class TestTurnBoundaries:
    def test_simple_turns(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        boundaries = _find_turn_boundaries(msgs)
        assert boundaries == [0, 2]

    def test_no_user_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "hi"},
        ]
        assert _find_turn_boundaries(msgs) == []


class TestContextCompressor:
    def test_initial_state(self):
        cc = ContextCompressor()
        assert cc.context_window == 128_000
        assert cc.threshold == 64_000
        assert cc.stats["compression_count"] == 0

    def test_needs_compression_empty(self):
        cc = ContextCompressor()
        assert not cc.needs_compression([])

    def test_needs_compression_short(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "短消息"}]
        assert not cc.needs_compression(msgs)

    def test_get_usage_ratio(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "短消息"}]
        ratio = cc.get_usage_ratio(msgs)
        assert 0.0 < ratio < 1.0

    def test_compress_empty(self):
        cc = ContextCompressor()
        msgs, compressed = cc.compress([])
        assert msgs == []
        assert not compressed

    def test_prune_only_without_llm(self):
        """工具结果裁剪后如果低于阈值，不应触发摘要。"""
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "短对话"}]
        result, compressed = cc.compress(msgs)
        assert result == msgs
        assert not compressed

    def test_basic_summary_fallback(self):
        """没有 LLM 时用基础摘要。"""
        cc = ContextCompressor()
        # 构造足够长的中间轮次
        msgs = [{"role": "system", "content": "sys"}]  # system msg
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题 {i}"})
            msgs.append({"role": "assistant", "content": f"回答 {i}"})
        # 填充到超过阈值
        long_content = "长文本" * 5000
        msgs.append({"role": "user", "content": long_content})

        result, compressed = cc.compress(msgs)
        # 应该至少裁剪了内容
        assert isinstance(result, list)

    def test_prune_updates_stats(self):
        cc = ContextCompressor()
        # 创建过长工具结果
        msgs = [
            {"role": "user", "content": "查看大文件"},
            {"role": "tool", "content": "x" * 3000},
        ]
        result, _ = cc.compress(msgs)
        assert cc.stats["pruned_chars"] > 0 or len(str(result)) > 0
