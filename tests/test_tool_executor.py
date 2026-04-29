"""测试工具执行器: 并行/串行调度 + 顺序保持。[H+原创]"""

import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed

from tiangong.core.tool_executor import (
    ToolExecutor,
    _is_readonly_command,
    _MAX_TOOL_WORKERS,
)


class TestReadonlyDetection:
    def test_readonly_commands(self):
        assert _is_readonly_command("ls -la")
        assert _is_readonly_command("cat file.txt")
        assert _is_readonly_command("echo hello")
        assert _is_readonly_command("which python3")
        assert _is_readonly_command("git status")
        assert _is_readonly_command("brew list")

    def test_destructive_commands(self):
        assert not _is_readonly_command("rm -rf /tmp/test")
        assert not _is_readonly_command("sudo ls")
        assert not _is_readonly_command("kill 1234")
        assert not _is_readonly_command("chmod 777 file")
        assert not _is_readonly_command("git push origin main")
        assert not _is_readonly_command("pip install requests")
        assert not _is_readonly_command("npm install react")
        assert not _is_readonly_command("brew install curl")
        assert not _is_readonly_command("shutdown now")
        assert not _is_readonly_command("dd if=/dev/zero of=file")


class TestGrouping:
    def test_single_tool(self):
        executor = ToolExecutor()
        tcs = [{"function": {"name": "bash", "arguments": '{"command": "ls"}'}, "id": "1"}]
        groups = executor._group_for_parallel(tcs)
        # 只读命令 → 进并行组
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_mixed_safety(self):
        executor = ToolExecutor()
        tcs = [
            {"function": {"name": "bash", "arguments": '{"command": "rm file"}'}, "id": "1"},
            {"function": {"name": "bash", "arguments": '{"command": "ls"}'}, "id": "2"},
        ]
        groups = executor._group_for_parallel(tcs)
        # rm 进串行组（单独），ls 进并行组
        assert len(groups) == 2
        # 第一个组是串行（rm）
        assert len(groups[0]) == 1
        # 第二个组是并行（ls）
        assert len(groups[1]) == 1

    def test_all_parallel_safe(self):
        executor = ToolExecutor()
        tcs = [
            {"function": {"name": "memory", "arguments": '{"query": "测试"}'}, "id": "1"},
            {"function": {"name": "task", "arguments": '{"action": "list"}'}, "id": "2"},
        ]
        groups = executor._group_for_parallel(tcs)
        # 全部并行安全
        assert len(groups) == 1
        assert len(groups[0]) == 2


class TestParallelOrderPreservation:
    """验证并行执行后 result[i] 对应 tool_calls[i]（修复严重 bug）。"""

    def test_parallel_results_keep_order(self):
        """用不同延迟的工具验证结果顺序与输入一致。"""
        executor = ToolExecutor(max_workers=2)

        # 工具 A 慢（sleep 0.2s），工具 B 快（sleep 0.05s）
        tcs = [
            {
                "function": {"name": "bash", "arguments": '{"command": "sleep 0.2 && echo slow"}', "timeout": 5},
                "id": "call_slow",
            },
            {
                "function": {"name": "bash", "arguments": '{"command": "sleep 0.05 && echo fast"}', "timeout": 5},
                "id": "call_fast",
            },
        ]

        results = executor._execute_parallel(tcs)

        # 结果顺序必须与输入顺序一致
        assert len(results) == 2
        assert results[0]["tool_call_id"] == "call_slow", \
            f"位置0应该是call_slow（慢任务），实际是 {results[0]['tool_call_id']}"
        assert results[1]["tool_call_id"] == "call_fast", \
            f"位置1应该是call_fast（快任务），实际是 {results[1]['tool_call_id']}"

    def test_parallel_results_order_with_3_tools(self):
        """3个工具确认顺序保持。"""
        executor = ToolExecutor(max_workers=3)
        tcs = [
            {"function": {"name": "bash", "arguments": '{"command": "sleep 0.15 && echo A"}', "timeout": 5}, "id": "A"},
            {"function": {"name": "bash", "arguments": '{"command": "sleep 0.05 && echo B"}', "timeout": 5}, "id": "B"},
            {"function": {"name": "bash", "arguments": '{"command": "sleep 0.1 && echo C"}', "timeout": 5}, "id": "C"},
        ]
        results = executor._execute_parallel(tcs)
        assert len(results) == 3
        for i, tc in enumerate(tcs):
            assert results[i]["tool_call_id"] == tc["id"], \
                f"位置{i}: 期望 {tc['id']}，实际 {results[i]['tool_call_id']}"
