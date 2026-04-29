"""终端命令工具 — Agent 的系统操作能力.[H] 集成命令审批。"""

import json
import logging
import subprocess
import os
from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

BASH_SCHEMA = {
    "name": "bash",
    "description": (
        "在 macOS 系统上执行终端命令。"
        "用于读取文件、运行程序、安装包、查询系统信息等。"
        "命令在非交互式 shell 中运行，工作目录在调用间保持。"
        "长时间运行的命令会在 120 秒后超时。"
        "危险命令（如 rm -rf /、sudo、chmod 777）会被自动拦截。"
        "支持后台执行——耗时命令可异步运行，完成后通知。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 bash 命令。",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数（默认 120）。",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "是否在后台异步执行。适用于 npm install、pip install、docker build 等耗时命令。",
            },
            "background_timeout": {
                "type": "integer",
                "description": "后台任务超时秒数（默认 600，即10分钟）。",
            },
        },
        "required": ["command"],
    },
}

_DEFAULT_CWD = os.path.expanduser("~")


def bash_tool(args: dict, **kwargs) -> str:
    command = args.get("command", "")
    timeout = args.get("timeout", 120)
    run_in_background = args.get("run_in_background", False)
    background_timeout = args.get("background_timeout", 600)
    cwd = kwargs.get("cwd", _DEFAULT_CWD)

    if not command.strip():
        return tool_error("命令不能为空。")

    # ── 命令安全审批 [H] ──
    try:
        from tiangong.guard.command_approval import approver
        level, reasons = approver.check(command)

        if level == "dangerous":
            return tool_error(
                f"🚫 命令被安全拦截: {'; '.join(reasons)}\n"
                f"如需强制执行，请在终端手动执行。"
            )
        elif level == "warning":
            logger.info("命令需注意: %s — %s", command[:100], "; ".join(reasons))
    except Exception:
        pass

    # ── 后台执行 [原创] ──
    if run_in_background:
        from tiangong.core.background_task import get_bg_manager
        manager = get_bg_manager()
        task_id = manager.submit(
            command=command,
            cwd=str(cwd),
            timeout=background_timeout,
        )
        if not task_id:
            return tool_error("后台任务队列已满（最多10个），请等待其他任务完成。")

        return tool_result({
            "background": True,
            "task_id": task_id,
            "command": command[:200],
            "message": f"任务已在后台启动。使用 task_id={task_id} 查询状态。",
            "hint": "使用 后台任务 工具查询状态，或继续其他工作等待完成通知。",
        })

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env={**os.environ},
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if not output:
            output = f"(退出码: {result.returncode})"

        max_chars = 50000
        if len(output) > max_chars:
            output = output[:max_chars] + f"\n... (已截断，超出 {len(output) - max_chars} 字符)"

        return tool_result({
            "stdout": result.stdout[:max_chars] if result.stdout else "",
            "stderr": result.stderr[:max_chars] if result.stderr else "",
            "exit_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return tool_error(f"命令超时（{timeout}秒）。")
    except Exception as e:
        return tool_error(f"命令执行失败: {e}")


registry.register(
    name="bash",
    toolset="核心",
    schema=BASH_SCHEMA,
    handler=bash_tool,
    description="执行 macOS 终端命令",
    emoji="💻",
    display_name="终端命令",
)
