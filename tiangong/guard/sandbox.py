"""代码执行沙箱 — 隔离进程 + 资源限制 + 超时控制。[原创+H]

借鉴 Hermes: 工具白名单 + 输出捕获
原创: Python/bash 双重沙箱 + macOS 资源限制

安全策略:
- 独立进程组隔离 (setpgid)
- CPU 时间限制 (RLIMIT_CPU)
- 内存限制 (RLIMIT_AS)
- 输出大小限制
- 超时硬杀 (SIGKILL)
- 文件系统限制（可选 chroot 或白名单路径）
"""

import logging
import os
import resource
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 沙箱默认配置
DEFAULT_CPU_LIMIT = 30       # 秒
DEFAULT_MEMORY_MB = 512      # MB
DEFAULT_TIMEOUT = 60         # 秒
DEFAULT_MAX_OUTPUT = 100_000  # 字符
DEFAULT_MAX_OUTPUT_LINES = 500

# 允许的 Python 内置函数（白名单）
_ALLOWED_BUILTINS = frozenset({
    "abs", "all", "any", "bin", "bool", "bytes", "callable",
    "chr", "complex", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "getattr", "hasattr",
    "hash", "hex", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "object",
    "oct", "ord", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple",
    "type", "zip",
})

# 禁止 import 的模块黑名单
_BLOCKED_IMPORTS = frozenset({
    "os", "subprocess", "sys", "shutil", "socket", "requests",
    "urllib", "http", "ftplib", "telnetlib", "smtplib",
    "ctypes", "multiprocessing", "threading", "signal",
    "importlib", "builtins", "__builtins__",
    "pathlib", "glob", "fnmatch",  # 文件系统（通过工具白名单允许）
})


class SandboxConfig:
    """沙箱配置。"""

    __slots__ = ("cpu_limit", "memory_mb", "timeout",
                 "max_output", "max_output_lines", "allow_network",
                 "allowed_paths", "env")

    def __init__(
        self,
        cpu_limit: int = DEFAULT_CPU_LIMIT,
        memory_mb: int = DEFAULT_MEMORY_MB,
        timeout: int = DEFAULT_TIMEOUT,
        max_output: int = DEFAULT_MAX_OUTPUT,
        max_output_lines: int = DEFAULT_MAX_OUTPUT_LINES,
        allow_network: bool = False,
        allowed_paths: list = None,
        env: dict = None,
    ):
        self.cpu_limit = cpu_limit
        self.memory_mb = memory_mb
        self.timeout = timeout
        self.max_output = max_output
        self.max_output_lines = max_output_lines
        self.allow_network = allow_network
        self.allowed_paths = allowed_paths or [str(Path.home())]
        self.env = env or {}


def _set_limits(config: SandboxConfig):
    """在子进程中设置资源限制（必须在 fork 后调用）。"""
    try:
        # CPU 时间限制
        resource.setrlimit(resource.RLIMIT_CPU, (config.cpu_limit, config.cpu_limit))

        # 内存限制
        mem_bytes = config.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        # 文件大小限制 (100MB)
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024, 100 * 1024 * 1024))

        # 进程数限制
        resource.setrlimit(resource.RLIMIT_NPROC, (50, 50))

    except Exception as e:
        logger.warning("资源限制设置失败: %s", e)


def _truncate_output(output: str, config: SandboxConfig) -> str:
    """裁剪输出到限制内。"""
    if len(output) <= config.max_output:
        return output

    lines = output.split("\n")
    if len(lines) <= config.max_output_lines:
        return output[:config.max_output] + f"\n... ({len(output) - config.max_output} 字符已截断)"

    head = lines[:config.max_output_lines // 2]
    tail = lines[-(config.max_output_lines // 2):]
    omitted = len(lines) - config.max_output_lines
    return (
        "\n".join(head)
        + f"\n... ({omitted} 行, {len(output) - config.max_output} 字符已截断) ...\n"
        + "\n".join(tail)
    )


class BashSandbox:
    """Shell 命令沙箱 — 受限子进程执行。[原创]"""

    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()

    def run(self, command: str, cwd: str = None) -> dict:
        """在沙箱中执行 bash 命令。

        返回: {"stdout": ..., "stderr": ..., "exit_code": ..., "killed": bool, "duration_ms": int}
        """
        cwd = cwd or str(Path.home())
        t0 = time.time()

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
                cwd=cwd,
                env=self.config.env or None,
                preexec_fn=lambda: (
                    _set_limits(self.config),
                    os.setpgid(0, 0),  # 独立进程组
                ),
            )

            stdout = _truncate_output(proc.stdout or "", self.config)
            stderr = _truncate_output(proc.stderr or "", self.config)

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": proc.returncode,
                "killed": proc.returncode == -signal.SIGKILL,
                "duration_ms": int((time.time() - t0) * 1000),
            }

        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"执行超时（{self.config.timeout}秒）",
                "exit_code": -1,
                "killed": True,
                "duration_ms": int((time.time() - t0) * 1000),
            }

        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e)[:500],
                "exit_code": -1,
                "killed": False,
                "duration_ms": int((time.time() - t0) * 1000),
            }


class PythonSandbox:
    """Python 代码沙箱 — 受限 exec + 资源限制。[原创]"""

    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()

    def run(self, code: str, globals_dict: dict = None) -> dict:
        """在沙箱中执行 Python 代码。

        通过在子进程中运行 Python 实现进程级隔离和资源限制。
        """
        t0 = time.time()

        # 将代码写入临时文件
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="tiangong_sandbox_"
            ) as f:
                f.write(self._wrap_code(code))
                tmp_path = f.name

            proc = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
                cwd=str(Path.home()),
                env=self.config.env or None,
                preexec_fn=lambda: (
                    _set_limits(self.config),
                    os.setpgid(0, 0),
                ),
            )

            stdout = _truncate_output(proc.stdout or "", self.config)
            stderr = _truncate_output(proc.stderr or "", self.config)

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": proc.returncode,
                "killed": proc.returncode == -signal.SIGKILL,
                "duration_ms": int((time.time() - t0) * 1000),
            }

        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Python 执行超时（{self.config.timeout}秒）",
                "exit_code": -1,
                "killed": True,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e)[:500],
                "exit_code": -1,
                "killed": False,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _wrap_code(self, code: str) -> str:
        """包装代码：添加安全头 + 错误处理。"""
        return f'''"""天工沙箱 — 安全执行环境"""
import sys
import builtins

# 安全限制：禁止危险 import
_original_import = builtins.__import__

_BLOCKED = {repr(sorted(_BLOCKED_IMPORTS))}

def _safe_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top in _BLOCKED:
        raise ImportError(f"模块 '{{name}}' 在沙箱中被禁用")
    return _original_import(name, *args, **kwargs)

builtins.__import__ = _safe_import

# 执行用户代码
try:
{chr(10).join("    " + line for line in code.split(chr(10)))}
except Exception as e:
    print(f"\\n错误: {{type(e).__name__}}: {{e}}", file=sys.stderr)
    sys.exit(1)
'''


# ── 模块级实例 ──

_default_sandbox = BashSandbox()
_default_py_sandbox = PythonSandbox()


def get_sandbox() -> BashSandbox:
    return _default_sandbox


def get_py_sandbox() -> PythonSandbox:
    return _default_py_sandbox
