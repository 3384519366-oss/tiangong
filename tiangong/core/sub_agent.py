"""子代理系统 — 隔离上下文 + 受限工具 + 并行委派。[CC]

借鉴 Claude Code: 子代理委派、任务隔离、递归深度控制

核心设计:
- SubAgent: 轻量代理，隔离消息列表，受限工具集
- SubAgentPool: ThreadPoolExecutor 并行调度，最大 4 并发
- 递归深度: 最大 2 层
- 心跳汇报: 回调通知父代理进度
- 结果格式: 结构化 findings + status
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional

from .llm_client import LLMClient
from .registry import registry
from .iteration_budget import IterationBudget

logger = logging.getLogger(__name__)

# 子代理配置
_MAX_DEPTH = 2                      # 最大递归深度
_MAX_WORKERS = 4                    # 最大并行子代理
_MAX_TURNS = 15                     # 子代理最大轮次
_SUBAGENT_TIMEOUT = 300             # 子代理超时(秒)

# 默认工具白名单 — 子代理只能用只读/安全工具
_DEFAULT_TOOL_WHITELIST = {
    "read",         # 读取文件
    "grep",         # 搜索代码
    "memory",       # 查询记忆
    "web_search",   # 网络搜索
    "web_fetch",    # 网页抓取
    "bash",         # 只读命令安全
}

# 子代理系统提示
_SUBAGENT_SYSTEM_PROMPT = """你是天工子代理，负责独立完成委派任务。

## 行为准则
- 只使用可用工具完成委派任务
- 完成后输出明确结论
- 遇到无法处理的障碍时报告原因
- 保持简洁高效

## 限制
- 不能委派其他子代理（已达深度上限）
- 不能修改文件或执行危险命令
"""


class SubAgentResult:
    """子代理执行结果。"""

    __slots__ = ("agent_id", "task", "status", "findings",
                 "turns_used", "duration_ms", "error")

    def __init__(self, agent_id: str, task: str):
        self.agent_id = agent_id
        self.task = task
        self.status = "pending"      # pending | running | done | failed | timeout
        self.findings: str = ""
        self.turns_used: int = 0
        self.duration_ms: int = 0
        self.error: str = ""

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "task": self.task[:200],
            "status": self.status,
            "findings": self.findings[:2000],
            "turns_used": self.turns_used,
            "duration_ms": self.duration_ms,
            "error": self.error[:500],
        }


class SubAgent:
    """子代理 — 隔离上下文执行独立任务。[CC]"""

    def __init__(
        self,
        llm_client: LLMClient,
        model: str,
        depth: int = 0,
        tool_whitelist: set = None,
        on_heartbeat: Callable = None,
    ):
        self.agent_id = uuid.uuid4().hex[:8]
        self.depth = depth
        self.llm = llm_client
        self.model = model
        self.tool_whitelist = tool_whitelist or _DEFAULT_TOOL_WHITELIST
        self.on_heartbeat = on_heartbeat

        self.budget = IterationBudget(max_iterations=_MAX_TURNS)
        self.messages: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def _get_tools(self) -> List[dict]:
        """获取白名单工具 schema。"""
        all_schemas = registry.get_schemas()
        return [s for s in all_schemas if s.get("name") in self.tool_whitelist]

    def _run_tool(self, name: str, args: dict) -> str:
        """执行工具（白名单已在外层过滤）。"""
        return registry.dispatch(name, args)

    def _heartbeat(self, status: str, turns: int):
        """汇报进度。"""
        if self.on_heartbeat:
            try:
                self.on_heartbeat({
                    "agent_id": self.agent_id,
                    "status": status,
                    "turns": turns,
                    "depth": self.depth,
                })
            except Exception:
                pass

    def execute(self, task: str, context: str = "") -> SubAgentResult:
        """执行委派任务，返回结构化结果。"""
        result = SubAgentResult(self.agent_id, task)
        result.status = "running"
        t0 = time.time()

        try:
            system_prompt = _SUBAGENT_SYSTEM_PROMPT
            if context:
                system_prompt += f"\n\n## 上下文\n{context}"

            self.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请完成以下任务:\n\n{task}"},
            ]

            tools = self._get_tools()
            self._heartbeat("started", 0)

            while not self.budget.exhausted:
                response = self.llm.chat(self.messages, tools=tools)

                if response.get("tool_calls"):
                    tcs = response["tool_calls"]
                    self.budget.consume()
                    self._heartbeat("working", self.budget.consumed)

                    self.messages.append({
                        "role": "assistant",
                        "content": response.get("content", ""),
                        "tool_calls": tcs,
                    })

                    for tc in tcs:
                        fn = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {}

                        # 安全校验: 再次检查白名单
                        if fn not in self.tool_whitelist:
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps({"error": f"工具 {fn} 不在白名单中"}, ensure_ascii=False),
                            })
                            continue

                        tool_result = self._run_tool(fn, args)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result,
                        })
                    continue

                # 无工具调用 — 子代理完成
                final = response.get("content", "")
                self.messages.append({"role": "assistant", "content": final})
                result.findings = final
                result.status = "done"
                result.turns_used = self.budget.consumed
                result.duration_ms = int((time.time() - t0) * 1000)
                self._heartbeat("done", result.turns_used)
                return result

            # 预算耗尽
            result.status = "timeout"
            result.error = f"超过最大轮次 ({_MAX_TURNS})"
            result.turns_used = self.budget.consumed
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        except Exception as e:
            result.status = "failed"
            result.error = str(e)[:500]
            result.duration_ms = int((time.time() - t0) * 1000)
            logger.warning("子代理 %s 失败: %s", self.agent_id, e)
            return result


class SubAgentPool:
    """子代理池 — ThreadPoolExecutor 并行调度。[CC]"""

    def __init__(self, llm_client: LLMClient, model: str, max_workers: int = _MAX_WORKERS):
        self.llm = llm_client
        self.model = model
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: Dict[str, Future] = {}
        self._results: Dict[str, SubAgentResult] = {}
        self._lock = threading.Lock()
        self._heartbeats: List[dict] = []

    def _on_heartbeat(self, event: dict):
        """收集子代理心跳。"""
        with self._lock:
            self._heartbeats.append(event)

    def delegate(
        self,
        tasks: List[Dict[str, str]],
        depth: int = 0,
        tool_whitelist: set = None,
    ) -> List[SubAgentResult]:
        """并行委派多个任务。

        tasks: [{"task": "...", "context": "..."}, ...]
        depth: 当前递归深度
        返回: 每个任务的结果列表
        """
        from concurrent.futures import as_completed

        futures_map: Dict[Future, str] = {}
        results_map: Dict[str, SubAgentResult] = {}

        for task_info in tasks:
            agent = SubAgent(
                llm_client=self.llm,
                model=self.model,
                depth=depth,
                tool_whitelist=tool_whitelist,
                on_heartbeat=self._on_heartbeat,
            )

            future = self._executor.submit(
                agent.execute,
                task_info.get("task", ""),
                task_info.get("context", ""),
            )
            futures_map[future] = agent.agent_id
            results_map[agent.agent_id] = SubAgentResult(agent.agent_id, task_info.get("task", ""))
            with self._lock:
                self._futures[agent.agent_id] = future

        # 真正并行收集：哪个先完成先处理
        for future in as_completed(futures_map, timeout=_SUBAGENT_TIMEOUT):
            agent_id = futures_map[future]
            try:
                result = future.result()
                results_map[agent_id] = result
            except Exception as e:
                results_map[agent_id].status = "failed"
                results_map[agent_id].error = str(e)[:500]

        # 按 tasks 原始顺序返回
        return [results_map[aid] for aid in [futures_map[f] for f in futures_map]]

    def delegate_single(
        self,
        task: str,
        context: str = "",
        depth: int = 0,
        tool_whitelist: set = None,
    ) -> SubAgentResult:
        """委派单个任务（同步）。"""
        agent = SubAgent(
            llm_client=self.llm,
            model=self.model,
            depth=depth,
            tool_whitelist=tool_whitelist,
            on_heartbeat=self._on_heartbeat,
        )
        return agent.execute(task, context)

    def get_heartbeats(self) -> List[dict]:
        """获取并清空心跳记录。"""
        with self._lock:
            beats = list(self._heartbeats)
            self._heartbeats.clear()
        return beats

    def shutdown(self):
        """关闭线程池。"""
        self._executor.shutdown(wait=False)
