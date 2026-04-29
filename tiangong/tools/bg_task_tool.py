"""后台任务工具 — 查询、管理异步执行的后台命令。[原创]"""

import json
import logging

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

BG_TASK_SCHEMA = {
    "name": "bg_task",
    "description": (
        "管理后台异步任务。用于查询任务状态、列出所有任务、终止任务。"
        "当使用 终端命令 工具的 run_in_background 参数启动后台任务后，"
        "用此工具追踪进度和获取结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "list", "list_active", "kill", "notifications"],
                "description": "操作：status=查询状态, list=全部任务, list_active=活跃任务, kill=终止, notifications=获取完成通知",
            },
            "task_id": {
                "type": "string",
                "description": "任务 ID（status 和 kill 操作需要）",
            },
        },
        "required": ["action"],
    },
}


def bg_task_tool(args: dict, **kwargs) -> str:
    """管理后台任务。"""
    from tiangong.core.background_task import get_bg_manager

    action = args.get("action", "list")
    task_id = args.get("task_id", "")
    manager = get_bg_manager()

    try:
        if action == "status":
            if not task_id:
                return tool_error("task_id 不能为空。")
            status = manager.get_status(task_id)
            return tool_result(status)

        elif action == "list":
            tasks = manager.list_tasks()
            running = sum(1 for t in tasks if t["status"] in ("pending", "running"))
            return tool_result({
                "total": len(tasks),
                "running": running,
                "tasks": tasks,
            })

        elif action == "list_active":
            tasks = manager.list_active()
            return tool_result({
                "active": len(tasks),
                "tasks": tasks,
            })

        elif action == "kill":
            if not task_id:
                return tool_error("task_id 不能为空。")
            ok = manager.kill(task_id)
            return tool_result({"killed": ok, "task_id": task_id})

        elif action == "notifications":
            notes = manager.drain_notifications()
            return tool_result({
                "notifications": notes,
                "count": len(notes),
            })

        else:
            return tool_error(f"未知操作: {action}")

    except Exception as e:
        return tool_error(f"后台任务操作失败: {e}")


registry.register(
    name="bg_task",
    toolset="核心",
    schema=BG_TASK_SCHEMA,
    handler=bg_task_tool,
    description="管理后台异步任务（查询、列表、终止）",
    emoji="⏳",
    display_name="后台任务",
)
