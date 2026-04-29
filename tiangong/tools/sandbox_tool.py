"""沙箱工具 — Agent 的安全代码执行能力。[原创+H]

提供 Python 代码和 Shell 命令的安全执行环境。
所有代码在隔离子进程中运行，受资源限制和超时控制。
"""

import json
import logging

from tiangong.core.registry import registry, tool_result, tool_error
from tiangong.guard.sandbox import (
    BashSandbox, PythonSandbox, SandboxConfig,
    DEFAULT_TIMEOUT, DEFAULT_MAX_OUTPUT, DEFAULT_CPU_LIMIT, DEFAULT_MEMORY_MB,
)

logger = logging.getLogger(__name__)

SANDBOX_SCHEMA = {
    "name": "sandbox",
    "description": (
        "在安全沙箱中执行 Python 代码或 Shell 命令。"
        "沙箱提供进程隔离、资源限制（CPU/内存）、超时控制和输出裁剪。"
        "适用于运行数据分析、代码验证、文件处理等需要安全执行环境的场景。"
        "禁止 import os/subprocess/socket 等危险模块。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "bash"],
                "description": "代码语言：python 或 bash",
            },
            "code": {
                "type": "string",
                "description": "要执行的代码",
            },
            "timeout": {
                "type": "integer",
                "description": f"超时秒数（默认 {DEFAULT_TIMEOUT}）",
            },
            "cpu_limit": {
                "type": "integer",
                "description": f"CPU 时间限制秒数（默认 {DEFAULT_CPU_LIMIT}）",
            },
            "memory_mb": {
                "type": "integer",
                "description": f"内存限制 MB（默认 {DEFAULT_MEMORY_MB}）",
            },
        },
        "required": ["language", "code"],
    },
}


def sandbox_tool(args: dict, **kwargs) -> str:
    """在安全沙箱中执行代码。"""
    language = args.get("language", "python")
    code = args.get("code", "")
    timeout = args.get("timeout", DEFAULT_TIMEOUT)
    cpu_limit = args.get("cpu_limit", DEFAULT_CPU_LIMIT)
    memory_mb = args.get("memory_mb", DEFAULT_MEMORY_MB)

    if not code.strip():
        return tool_error("代码不能为空。")

    if language not in ("python", "bash"):
        return tool_error(f"不支持的语言: {language}。请使用 python 或 bash。")

    # 额外安全检查：危险模式
    if language == "python":
        dangerous = [
            "os.system", "subprocess.", "eval(", "exec(", "__import__",
            "open(", "socket.", "requests.", "urllib.", "shutil.rmtree",
            "os.remove", "os.unlink", "os.rmdir",
        ]
        for pattern in dangerous:
            if pattern in code:
                return tool_error(
                    f"代码包含危险模式 '{pattern}'，已在沙箱中禁用。"
                    f"如需文件操作请使用终端命令工具。"
                )

    config = SandboxConfig(
        timeout=min(timeout, 300),  # 硬上限 5 分钟
        cpu_limit=min(cpu_limit, 120),
        memory_mb=min(memory_mb, 1024),  # 硬上限 1GB
    )

    try:
        if language == "python":
            sandbox = PythonSandbox(config)
            result = sandbox.run(code)
        else:
            sandbox = BashSandbox(config)
            # bash 沙箱再进行命令审批检查
            from tiangong.guard.command_approval import approver
            level, reasons = approver.check(code)
            if level == "dangerous":
                return tool_error(
                    f"命令被安全拦截: {'; '.join(reasons)}\n"
                    f"危险命令不能在沙箱中执行。"
                )
            result = sandbox.run(code)

        output = result.get("stdout", "")
        errors = result.get("stderr", "")

        response = {
            "language": language,
            "exit_code": result["exit_code"],
            "killed": result["killed"],
            "duration_ms": result["duration_ms"],
        }

        if output:
            response["stdout"] = output[:DEFAULT_MAX_OUTPUT]
        if errors:
            response["stderr"] = errors[:DEFAULT_MAX_OUTPUT]

        return tool_result(response)

    except Exception as e:
        return tool_error(f"沙箱执行失败: {e}")


registry.register(
    name="sandbox",
    toolset="核心",
    schema=SANDBOX_SCHEMA,
    handler=sandbox_tool,
    description="在安全沙箱中执行 Python/Bash 代码",
    emoji="🧪",
    display_name="沙箱",
)
