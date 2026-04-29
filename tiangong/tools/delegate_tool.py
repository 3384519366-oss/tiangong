"""委派工具 — Agent 可生成子代理处理独立任务。[CC]

借鉴 Claude Code: 子代理隔离、工具白名单、并行委派
"""

import json
import logging
import threading
from typing import Any

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

DELEGATE_SCHEMA = {
    "name": "delegate",
    "description": (
        "委派一个或多个子代理独立完成任务。子代理拥有隔离的上下文和受限的工具集。"
        "用于并行搜索、独立分析、批量信息收集等场景。"
        "每个子代理会在完成后返回结构化结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "要委派的任务列表，每项包含 task 和可选的 context",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "子代理要完成的具体任务描述",
                        },
                        "context": {
                            "type": "string",
                            "description": "可选的背景上下文信息",
                        },
                    },
                    "required": ["task"],
                },
            },
            "parallel": {
                "type": "boolean",
                "description": "是否并行执行（默认 true）",
            },
            "tool_whitelist": {
                "type": "array",
                "items": {"type": "string"},
                "description": "子代理可用的工具名称列表，默认只读工具集",
            },
        },
        "required": ["tasks"],
    },
}

# 线程安全的委派深度追踪
_depth_local = threading.local()


def _get_depth() -> int:
    return getattr(_depth_local, "value", 0)


def _set_depth(val: int):
    _depth_local.value = val


# 共享子代理池（由 Agent 初始化时设置）
_pool = None


def set_delegate_pool(pool):
    """设置共享的子代理池，由 Agent 初始化时调用。"""
    global _pool
    _pool = pool


def _get_pool():
    """获取共享的子代理池。"""
    global _pool
    if _pool is not None:
        return _pool
    # 延迟创建
    from tiangong.core.sub_agent import SubAgentPool
    from tiangong.core.llm_client import LLMClient

    llm = LLMClient()
    _pool = SubAgentPool(llm_client=llm, model=llm.model)
    return _pool


def delegate_tool(args: dict, **kwargs) -> str:
    """执行委派任务。[CC]"""

    from tiangong.core.sub_agent import _MAX_DEPTH, _DEFAULT_TOOL_WHITELIST

    tasks = args.get("tasks", [])
    parallel = args.get("parallel", True)
    tool_whitelist = set(args.get("tool_whitelist", []) or [])

    if not tasks:
        return tool_error("任务列表不能为空。")

    if not tool_whitelist:
        tool_whitelist = _DEFAULT_TOOL_WHITELIST

    depth = _get_depth()
    if depth >= _MAX_DEPTH:
        return tool_error(f"已达到最大委派深度 ({_MAX_DEPTH})，无法继续委派。")

    try:
        _set_depth(depth + 1)
        pool = _get_pool()

        current_depth = _get_depth()
        logger.info("委派 %d 个子代理 (并行=%s, 深度=%d)", len(tasks), parallel, current_depth)

        if parallel and len(tasks) > 1:
            results = pool.delegate(tasks, depth=current_depth, tool_whitelist=tool_whitelist)
        else:
            results = []
            for task_info in tasks:
                r = pool.delegate_single(
                    task_info.get("task", ""),
                    task_info.get("context", ""),
                    depth=current_depth,
                    tool_whitelist=tool_whitelist,
                )
                results.append(r)

        # 格式化返回
        output = {
            "total": len(results),
            "completed": sum(1 for r in results if r.status == "done"),
            "failed": sum(1 for r in results if r.status in ("failed", "timeout")),
            "results": [],
        }

        for r in results:
            item = r.to_dict()
            output["results"].append(item)
            logger.info("子代理 %s: %s (%d轮, %dms)",
                        r.agent_id, r.status, r.turns_used, r.duration_ms)

        return tool_result(output)

    except Exception as e:
        return tool_error(f"委派执行失败: {e}")
    finally:
        _set_depth(_get_depth() - 1)


def get_delegate_depth() -> int:
    """获取当前委派深度。"""
    return _get_depth()


registry.register(
    name="delegate",
    toolset="核心",
    schema=DELEGATE_SCHEMA,
    handler=delegate_tool,
    description="委派子代理执行独立任务",
    emoji="🤝",
    display_name="委派",
)
