"""后台任务系统 — 异步命令执行 + 状态追踪 + 完成通知。[原创]

Agent 可将耗时命令（构建、安装、训练等）放到后台执行，
继续处理其他任务，完成后收到通知。
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_BG_TASKS = 10
_DEFAULT_BG_TIMEOUT = 600  # 10 分钟


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    KILLED = "killed"


class BackgroundTask:
    """单个后台任务。"""

    __slots__ = ("task_id", "command", "cwd", "timeout",
                 "status", "started_at", "finished_at",
                 "stdout", "stderr", "exit_code", "result",
                 "_thread", "_process", "_on_complete")

    def __init__(self, command: str, cwd: str = "~",
                 timeout: int = _DEFAULT_BG_TIMEOUT,
                 on_complete: Callable = None):
        self.task_id = uuid.uuid4().hex[:10]
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.status = TaskStatus.PENDING
        self.started_at: float = 0
        self.finished_at: float = 0
        self.stdout: str = ""
        self.stderr: str = ""
        self.exit_code: int = -1
        self.result: dict = {}
        self._thread: threading.Thread = None
        self._process = None
        self._on_complete = on_complete

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "command": self.command[:200],
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int((self.finished_at - self.started_at) * 1000) if self.started_at else 0,
            "exit_code": self.exit_code,
            "stdout_tail": (self.stdout or "")[-500:],
            "stderr_tail": (self.stderr or "")[-200:],
        }

    def _run(self):
        """在线程中执行命令。"""
        import os
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()

        try:
            self._process = subprocess.run(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=os.path.expanduser(self.cwd),
                env={**os.environ},
            )
            self.stdout = (self._process.stdout or "")[:50000]
            self.stderr = (self._process.stderr or "")[:10000]
            self.exit_code = self._process.returncode
            self.status = TaskStatus.DONE if self.exit_code == 0 else TaskStatus.FAILED

        except subprocess.TimeoutExpired:
            self.status = TaskStatus.TIMEOUT
            self.stderr = f"任务超时（{self.timeout}秒）"
        except Exception as e:
            self.status = TaskStatus.FAILED
            self.stderr = str(e)[:1000]
        finally:
            self.finished_at = time.time()

        if self._on_complete:
            try:
                self._on_complete(self)
            except Exception:
                pass

    def start(self):
        """启动后台执行。"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def kill(self):
        """终止任务。"""
        if self._process and self._process.poll() is None:
            try:
                self._process.kill()
                self.status = TaskStatus.KILLED
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)


class BackgroundTaskManager:
    """后台任务管理器 — 追踪所有异步任务。"""

    def __init__(self, max_tasks: int = _MAX_BG_TASKS):
        self.max_tasks = max_tasks
        self._tasks: Dict[str, BackgroundTask] = {}
        self._completed: List[BackgroundTask] = []
        self._lock = threading.Lock()
        self._notifications: List[dict] = []

    def submit(self, command: str, cwd: str = "~",
               timeout: int = _DEFAULT_BG_TIMEOUT) -> str:
        """提交后台任务，返回 task_id。"""
        with self._lock:
            # 清理已完成的旧任务
            active = {tid: t for tid, t in self._tasks.items() if t.is_running()}
            if len(active) >= self.max_tasks:
                return ""  # 任务队列满

            task = BackgroundTask(
                command=command,
                cwd=cwd,
                timeout=timeout,
                on_complete=self._on_task_complete,
            )
            self._tasks[task.task_id] = task

        task.start()
        logger.info("后台任务已启动: %s — %s", task.task_id, command[:100])
        return task.task_id

    def _on_task_complete(self, task: BackgroundTask):
        """任务完成回调。"""
        with self._lock:
            self._completed.append(task)
            notification = {
                "task_id": task.task_id,
                "status": task.status.value,
                "exit_code": task.exit_code,
                "duration_ms": int((task.finished_at - task.started_at) * 1000),
            }
            self._notifications.append(notification)
        logger.info("后台任务完成: %s — %s", task.task_id, task.status.value)

    def get(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> dict:
        """查询任务状态。"""
        task = self.get(task_id)
        if task:
            return task.to_dict()
        return {"error": f"任务 {task_id} 不存在"}

    def list_tasks(self) -> List[dict]:
        """列出所有任务。"""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def list_active(self) -> List[dict]:
        """列出活跃任务。"""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values() if t.is_running()]

    def drain_notifications(self) -> List[dict]:
        """获取并清空完成通知。"""
        with self._lock:
            notes = list(self._notifications)
            self._notifications.clear()
        return notes

    def kill(self, task_id: str) -> bool:
        """终止任务。"""
        task = self.get(task_id)
        if task:
            task.kill()
            return True
        return False

    def cleanup(self):
        """清理已完成超过1小时的任务。"""
        with self._lock:
            cutoff = time.time() - 3600
            self._tasks = {
                tid: t for tid, t in self._tasks.items()
                if t.is_running() or t.finished_at > cutoff
            }


# 模块级单例
_bg_manager: Optional[BackgroundTaskManager] = None


def get_bg_manager() -> BackgroundTaskManager:
    global _bg_manager
    if _bg_manager is None:
        _bg_manager = BackgroundTaskManager()
    return _bg_manager
