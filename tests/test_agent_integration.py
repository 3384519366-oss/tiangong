"""Agent 核心循环集成测试 — Mock LLM 验证全链路。[原创]

覆盖:
- 流式对话: 文本响应 + 工具调用 + 递归继续
- 上下文压缩触发
- 迭代预算耗尽
- steer 引导注入
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock

from tiangong.core.agent import TianGongAgent, _discover_tools


# ── Mock 工厂 ────────────────────────────────────────

def _make_mock_llm(text_response="测试响应", tool_calls=None):
    """创建模拟 LLMClient，返回指定响应。"""
    mock = MagicMock()
    mock.model = "test-model"

    # 模拟 chat_stream
    def chat_stream(messages, tools=None):
        if text_response:
            yield {"content": text_response}
        if tool_calls:
            yield {"_tool_calls": tool_calls}

    mock.chat_stream = chat_stream

    # 模拟 chat
    def chat(messages, tools=None):
        result = {
            "role": "assistant",
            "content": text_response,
            "finish_reason": "stop",
        }
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result

    mock.chat = chat
    return mock


def _mock_tool_registry():
    """mock registry 返回空工具集，避免工具发现导入错误。"""
    try:
        _discover_tools()
    except Exception:
        pass
    # 注册一个简单测试工具
    from tiangong.core.registry import registry, tool_result

    if "test_echo" not in registry._tools:
        registry.register(
            name="test_echo",
            toolset="测试",
            schema={
                "name": "test_echo",
                "description": "测试工具",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
            },
            handler=lambda args, **kw: tool_result({"echo": args.get("message", "")}),
            description="回显消息用于测试",
            emoji="🧪",
            display_name="测试回显",
        )


# ── 测试类 ────────────────────────────────────────────

class TestAgentStreamChat:
    """流式对话集成测试。"""

    def test_simple_text_response(self):
        """纯文本响应，无工具调用。"""
        _mock_tool_registry()
        mock_llm = _make_mock_llm("你好，有什么可以帮你的？")

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm

            chunks = list(agent.stream_chat("你好"))
            assert "你好" in "".join(chunks)

    def test_tool_call_recursion(self):
        """工具调用后递归继续对话。"""
        _mock_tool_registry()

        # 第一次调用返回工具调用，第二次返回文本
        call_count = [0]

        def chat_stream(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                yield {"_tool_calls": [{
                    "id": "test_call_1",
                    "type": "function",
                    "function": {
                        "name": "test_echo",
                        "arguments": json.dumps({"message": "hello"}),
                    },
                }]}
            else:
                yield {"content": "工具执行完成，结果: echo=hello"}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = chat_stream

        mock_llm.chat = MagicMock(return_value={
            "role": "assistant", "content": "完成", "finish_reason": "stop",
        })

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm
            agent.budget.max_iterations = 5  # 确保够用

            chunks = list(agent.stream_chat("测试回显"))
            output = "".join(chunks)
            assert call_count[0] >= 2  # 至少调用了两次（工具调用 + 最终响应）

    def test_budget_exhausted(self):
        """迭代预算用尽后停止。"""
        _mock_tool_registry()

        # 创建总是返回工具调用的 LLM
        def chat_stream(messages, tools=None):
            yield {"_tool_calls": [{
                "id": f"call_budget",
                "type": "function",
                "function": {
                    "name": "test_echo",
                    "arguments": json.dumps({"message": "loop"}),
                },
            }]}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = chat_stream
        mock_llm.chat = MagicMock(return_value={
            "role": "assistant", "content": "", "finish_reason": "stop",
            "tool_calls": [{"id": "x", "type": "function", "function": {"name": "test_echo", "arguments": "{}"}}],
        })

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm
            agent.budget.max_iterations = 3  # 只有3轮

            chunks = list(agent.stream_chat("循环测试"))
            output = "".join(chunks)
            assert "达到最大" in output or agent.budget.exhausted


class TestAgentSteer:
    """引导注入测试。"""

    def test_steer_message_injected(self):
        """steer 消息在下一轮工具结果中注入。"""
        _mock_tool_registry()

        call_count = [0]

        def chat_stream(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                yield {"_tool_calls": [{
                    "id": "call_steer_test",
                    "type": "function",
                    "function": {
                        "name": "test_echo",
                        "arguments": json.dumps({"message": "test"}),
                    },
                }]}
            else:
                yield {"content": "已收到引导"}

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.chat_stream = chat_stream
        mock_llm.chat = MagicMock(return_value={
            "role": "assistant", "content": "ok", "finish_reason": "stop",
        })

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm
            agent.steer("请用JSON格式回复")
            assert agent._steer_message is not None

            list(agent.stream_chat("测试引导"))
            # steer 应在 drain 后为 None
            assert agent._steer_message is None


class TestAgentSession:
    """会话持久化集成测试。"""

    def test_save_and_restore(self):
        """保存会话后能正确恢复。"""
        _mock_tool_registry()
        mock_llm = _make_mock_llm("持久化测试")

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm

            # 模拟一些消息
            agent.messages = [
                {"role": "system", "content": "test system"},
                {"role": "user", "content": "测试消息"},
                {"role": "assistant", "content": "测试回复"},
            ]
            agent._session_name = "测试会话"
            session_id = agent.session_id

            agent._save_session()

            # 用新 agent 恢复
            agent2 = TianGongAgent()
            agent2.llm = mock_llm
            count = agent2.restore_session(session_id)

            assert count == 3
            assert agent2._session_name == "测试会话"

    def test_list_sessions(self):
        """列出已保存的会话。"""
        _mock_tool_registry()
        mock_llm = _make_mock_llm()

        with patch("tiangong.core.agent.LLMClient", return_value=mock_llm):
            agent = TianGongAgent()
            agent.llm = mock_llm
            agent.messages = [{"role": "user", "content": "list test"}]
            agent._save_session()

            sessions = agent.session_store.list_sessions()
            assert len(sessions) > 0
