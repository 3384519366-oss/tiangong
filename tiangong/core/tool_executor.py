"""工具执行器 — 并行/串行调度 + 安全判断 + 自动错误恢复。[H+原创]

借鉴 Hermes 的并行执行模式：
- 只读工具始终可并行
- 交互式工具强制串行
- 文件工具同目录串行、不同目录并行
- 终端命令安全启发式判断

原创扩展：
- 自动错误恢复：工具失败分析 + 智能重试
- 错误上下文注入：帮助 LLM 理解并修正错误
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Callable

from .registry import registry

logger = logging.getLogger(__name__)

_MAX_TOOL_WORKERS = 8

# 必须串行执行的工具（交互式）
_NEVER_PARALLEL = {"clarify"}

# 只读工具，始终可安全并行
_PARALLEL_SAFE = {
    "memory",       # 记忆搜索/读取
    "task",         # 任务列表/查看
    "bash",         # 需要结合命令内容判断
}

# 可能涉及文件写入的工具 — 需要路径分析
_PATH_SCOPED = {"bash"}


def _is_readonly_command(command: str) -> bool:
    """判断终端命令是否只读。"""
    cmd_clean = command.strip().lower()
    destructive = [
        "rm ", "rmdir ", "mv ", ">", ">>",
        "sudo ", "chmod ", "chown ",
        "kill ", "pkill ", "killall ",
        "shutdown", "reboot", "halt",
        "mkfs", "dd ", "format",
        "pip install", "pip3 install", "npm install", "brew install",
        "git push", "git commit",
        "curl", "wget",
    ]
    for pattern in destructive:
        if pattern in cmd_clean:
            return False
    return True


class ToolExecutor:
    """工具执行调度器——智能并行/串行切换 + 自动错误恢复。"""

    def __init__(self, max_workers: int = _MAX_TOOL_WORKERS):
        self.max_workers = max_workers
        from ..guard.error_recovery import RetryController
        self.retry = RetryController()

    def execute_batch(self, tool_calls: List[dict]) -> List[dict]:
        """执行一批工具调用。自动判断并行或串行。"""
        if len(tool_calls) <= 1:
            # 单个工具直接执行
            return [self._execute_one(tc) for tc in tool_calls]

        # 分析是否可以并行
        groups = self._group_for_parallel(tool_calls)

        if len(groups) == 1:
            # 全部可并行
            return self._execute_parallel(tool_calls)
        else:
            # 分批串行，组内并行
            results = []
            for group in groups:
                if len(group) == 1:
                    results.append(self._execute_one(group[0]))
                else:
                    results.extend(self._execute_parallel(group))
            return results

    def _group_for_parallel(self, tool_calls: List[dict]) -> List[List[dict]]:
        """将工具调用分组——同组内可并行，组间串行。"""
        serial_group = []
        parallel_group = []

        for tc in tool_calls:
            name = tc["function"]["name"]
            if name in _NEVER_PARALLEL:
                serial_group.append(tc)
            elif name in _PATH_SCOPED:
                args = self._safe_parse_args(tc)
                command = args.get("command", "")
                if not _is_readonly_command(command):
                    serial_group.append(tc)
                else:
                    parallel_group.append(tc)
            else:
                parallel_group.append(tc)

        result = []
        # 每个串行工具单独一组
        for tc in serial_group:
            result.append([tc])
        # 并行工具在一组
        if parallel_group:
            result.append(parallel_group)
        return result

    def _execute_parallel(self, tool_calls: List[dict]) -> List[dict]:
        """并行执行多个工具调用，保持输入顺序返回。"""
        results: List[dict] = [None] * len(tool_calls)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(tool_calls))) as pool:
            future_to_idx = {}
            for i, tc in enumerate(tool_calls):
                future = pool.submit(self._execute_one, tc)
                future_to_idx[future] = i

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result(timeout=120)
                except Exception as e:
                    results[idx] = {
                        "tool_call_id": tool_calls[idx]["id"],
                        "content": json.dumps(
                            {"error": f"并行执行失败: {e}"}, ensure_ascii=False
                        ),
                    }
        return results

    def _execute_one(self, tc: dict) -> dict:
        """执行单个工具调用，带自动错误恢复。[原创]"""
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except Exception:
            args = {}

        # 带重试的执行
        result = self._execute_with_recovery(name, args)

        # 注入上一轮错误上下文帮助 LLM 理解
        error_context = self.retry.get_context_for_llm()
        if error_context and self._is_error_result(result):
            try:
                data = json.loads(result)
                data["_error_context"] = error_context
                result = json.dumps(data, ensure_ascii=False)
            except Exception:
                pass

        return {
            "tool_call_id": tc["id"],
            "content": result,
        }

    def _execute_with_recovery(self, name: str, args: dict) -> str:
        """执行工具并自动重试可恢复错误。[原创]"""
        from ..guard.error_recovery import wrap_with_recovery, analyze_error

        result = registry.dispatch(name, args)

        # 检查是否是错误
        if not self._is_error_result(result):
            return result

        # 解析错误内容
        try:
            data = json.loads(result)
            error_text = data.get("error", data.get("_error", ""))
        except Exception:
            return result

        if not error_text or not self.retry.should_retry(name):
            return result

        # 分析错误
        report = analyze_error(error_text)
        if not report.auto_fixable:
            self.retry.record_attempt(name, args, error_text, report)
            # 即使不重试，也附上分析
            try:
                data["_error_analysis"] = report.to_prompt_hint()
                return json.dumps(data, ensure_ascii=False)
            except Exception:
                return result

        # 执行重试
        for attempt in range(self.retry.max_retries - self.retry.get_retry_count(name)):
            import time
            time.sleep(self.retry.delay(name))
            self.retry.record_attempt(name, args, error_text, report)

            logger.info("自动重试 %s (第 %d/%d 次): %s",
                        name, attempt + 1, self.retry.max_retries, report.message)

            # 调整参数
            adjusted = self._adjust_args(name, args, report)
            result = registry.dispatch(name, adjusted)

            if not self._is_error_result(result):
                logger.info("重试成功: %s", name)
                return result

            # 更新错误信息用于下一次分析
            try:
                data = json.loads(result)
                error_text = data.get("error", data.get("_error", ""))
            except Exception:
                break

        return result

    @staticmethod
    def _is_error_result(result: str) -> bool:
        """判断工具结果是否为错误。"""
        try:
            data = json.loads(result)
            return bool(data.get("error") or data.get("_error"))
        except Exception:
            return False

    @staticmethod
    def _adjust_args(name: str, args: dict, report) -> dict:
        """根据错误报告调整工具参数。"""
        from ..guard.error_recovery import ErrorReport
        adjusted = dict(args)

        if report.category == "timeout":
            old = adjusted.get("timeout", 120)
            if isinstance(old, (int, float)):
                adjusted["timeout"] = min(old * 2, 600)

        elif report.category == "network_error":
            old = adjusted.get("timeout", 120)
            if isinstance(old, (int, float)):
                adjusted["timeout"] = min(old + 60, 600)

        return adjusted

    @staticmethod
    def _safe_parse_args(tc: dict) -> dict:
        try:
            import json
            return json.loads(tc["function"]["arguments"])
        except Exception:
            return {}


# 模块级单例
executor = ToolExecutor()
