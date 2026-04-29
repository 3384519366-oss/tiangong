"""任务管理工具 — Agent 可调用的任务跟踪系统。[CC]"""

import json
import threading
from typing import Dict, List, Optional

from tiangong.core.registry import registry, tool_error, tool_result


class Task:
    __slots__ = ("id", "subject", "description", "status", "blocks", "blocked_by")

    def __init__(self, task_id: str, subject: str, description: str = ""):
        self.id = task_id
        self.subject = subject
        self.description = description
        self.status = "pending"
        self.blocks: List[str] = []
        self.blocked_by: List[str] = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocks": self.blocks,
            "blockedBy": self.blocked_by,
        }


class TaskManager:
    """内存中的任务管理器，线程安全。"""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def add(self, subject: str, description: str = "", blocked_by: List[str] = None) -> dict:
        with self._lock:
            self._counter += 1
            tid = str(self._counter)
            task = Task(tid, subject, description)
            if blocked_by:
                task.blocked_by = blocked_by
                # 反向建立依赖
                for dep_id in blocked_by:
                    if dep_id in self._tasks:
                        self._tasks[dep_id].blocks.append(tid)
            self._tasks[tid] = task
            return task.to_dict()

    def update(self, task_id: str, status: str = None, subject: str = None,
               description: str = None, add_blocks: List[str] = None,
               add_blocked_by: List[str] = None) -> dict:
        with self._lock:
            if task_id not in self._tasks:
                return {"error": f"任务 {task_id} 不存在。"}
            task = self._tasks[task_id]
            if status:
                if status not in ("pending", "in_progress", "completed", "deleted"):
                    return {"error": f"无效状态: {status}。可用: pending, in_progress, completed, deleted"}
                task.status = status
            if subject:
                task.subject = subject
            if description:
                task.description = description
            if add_blocks:
                for bid in add_blocks:
                    if bid not in task.blocks:
                        task.blocks.append(bid)
                    if bid in self._tasks and task_id not in self._tasks[bid].blocked_by:
                        self._tasks[bid].blocked_by.append(task_id)
            if add_blocked_by:
                for bid in add_blocked_by:
                    if bid not in task.blocked_by:
                        task.blocked_by.append(bid)
                    if bid in self._tasks and task_id not in self._tasks[bid].blocks:
                        self._tasks[bid].blocks.append(task_id)
            return task.to_dict()

    def list(self, status: str = None) -> list:
        result = []
        for t in self._tasks.values():
            if t.status == "deleted":
                continue
            if status and t.status != status:
                continue
            result.append(t.to_dict())
        return sorted(result, key=lambda x: int(x["id"]))

    def get(self, task_id: str) -> Optional[dict]:
        t = self._tasks.get(task_id)
        return t.to_dict() if t else None


# 模块级单例
_manager = TaskManager()


def task_tool_handler(args: dict, **kwargs) -> str:
    """处理任务管理操作。"""
    action = args.get("action", "")
    task_id = args.get("task_id", "")
    subject = args.get("subject", "")
    description = args.get("description", "")
    status = args.get("status")
    blocked_by = args.get("blocked_by")
    add_blocks = args.get("add_blocks")
    add_blocked_by = args.get("add_blocked_by")

    if action == "add":
        if not subject:
            return tool_error("创建任务需要提供 subject。")
        result = _manager.add(subject, description, blocked_by)
        return json.dumps(result, ensure_ascii=False)

    elif action == "update":
        if not task_id:
            return tool_error("更新任务需要提供 task_id。")
        result = _manager.update(task_id, status, subject, description, add_blocks, add_blocked_by)
        return json.dumps(result, ensure_ascii=False)

    elif action == "list":
        result = _manager.list(status)
        if not result:
            return tool_result({"tasks": [], "message": "暂无任务。"})
        return tool_result({"tasks": result, "count": len(result)})

    elif action == "get":
        if not task_id:
            return tool_error("查看任务需要提供 task_id。")
        result = _manager.get(task_id)
        if not result:
            return tool_error(f"任务 {task_id} 不存在。")
        return json.dumps(result, ensure_ascii=False)

    else:
        return tool_error(f"未知操作: {action}。可用: add(创建), update(更新), list(列表), get(查看)。")


TASK_SCHEMA = {
    "name": "task",
    "description": (
        "管理会话中的任务列表。用于将复杂工作拆分为可跟踪的子任务。"
        "\n操作:\n"
        "- add: 创建新任务，可设置依赖关系\n"
        "- update: 更新任务状态 (pending→in_progress→completed)\n"
        "- list: 列出所有任务，可按状态过滤\n"
        "- get: 查看单个任务详情\n"
        "状态流转: pending → in_progress → completed"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "list", "get"],
                "description": "操作类型。"
            },
            "task_id": {
                "type": "string",
                "description": "任务 ID（update/get 操作需要）。"
            },
            "subject": {
                "type": "string",
                "description": "任务标题（add/update 操作）。"
            },
            "description": {
                "type": "string",
                "description": "任务描述（add/update 操作）。"
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "deleted"],
                "description": "任务状态（update 操作）。"
            },
            "blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "依赖的其他任务 ID 列表（add 操作）。"
            },
            "add_blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "被此任务阻塞的任务 ID 列表（update 操作）。"
            },
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "新增的依赖任务 ID 列表（update 操作）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="task",
    toolset="核心",
    schema=TASK_SCHEMA,
    handler=task_tool_handler,
    description="管理会话任务列表，支持依赖关系和状态流转",
    emoji="📋",
    display_name="任务",
)
