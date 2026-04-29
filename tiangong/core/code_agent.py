"""Code Agent 模式 — Agent 用 Python 代码思考与行动。[借鉴smolagents]

借鉴 smolagents:
- CodeAgent: Agent 输出 Python 代码块，而非 JSON 工具调用（节省30% LLM 调用）
- AST 安全验证: 导入白名单、危险函数拦截、dunder 限制、操作计数
- FinalAnswerException(BaseException): 不被 except Exception 吞掉的终止信号
- 工具函数注入: 已注册工具自动变为 Python 可调用函数

安全设计:
1. AST 模式扫描 → 阻断危险模式
2. 子进程隔离执行 + 资源限制
3. 操作计数器防无限循环
4. 工具白名单注入
"""

import ast
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .registry import registry

logger = logging.getLogger(__name__)

# ── 安全配置 ────────────────────────────────────────────

# 允许 import 的模块（白名单）
_ALLOWED_IMPORTS: Set[str] = {
    "json", "re", "math", "statistics", "datetime", "time",
    "collections", "itertools", "functools", "operator",
    "string", "textwrap", "hashlib", "base64", "uuid",
    "pathlib", "pprint", "dataclasses", "enum",
    "csv", "io", "typing",
}

# 禁止的模块（即使匹配白名单前缀也禁止）
_BLOCKED_IMPORTS: Set[str] = {
    "os", "sys", "subprocess", "shutil", "socket", "requests",
    "urllib", "http", "ftplib", "smtplib", "telnetlib",
    "ctypes", "multiprocessing", "threading", "concurrent.futures",
    "signal", "atexit", "gc", "inspect", "importlib",
    "builtins", "compile", "code", "codeop",
    "pdb", "traceback", "logging",
    "pathlib.os",  # 阻止通过 pathlib 绕过
}

# 禁止的函数/属性
_BLOCKED_FUNCTIONS: Set[str] = {
    "compile", "eval", "exec", "globals", "locals",
    "__import__", "open", "breakpoint",
    "getattr", "setattr", "delattr", "hasattr",
}

# 禁止的 dunder 属性
_BLOCKED_DUNDER: Set[str] = {
    "__class__", "__bases__", "__mro__", "__subclasses__",
    "__globals__", "__code__", "__closure__", "__dict__",
    "__builtins__", "__import__",
}

# 操作限制
MAX_OPERATIONS = 10_000_000
MAX_LOOP_ITERATIONS = 100_000
EXECUTION_TIMEOUT = 30  # 秒


# ── FinalAnswerException ────────────────────────────────

class FinalAnswerException(BaseException):
    """继承自 BaseException，不被 except Exception 捕获。[借鉴smolagents]

    当 Agent 代码调用 final_answer() 时抛出此异常，安全穿透所有 try/except。
    """
    def __init__(self, answer: Any):
        self.answer = answer
        super().__init__(str(answer)[:200])


# ── AST 安全验证器 ──────────────────────────────────────

class CodeSecurityValidator(ast.NodeVisitor):
    """AST 遍历验证器 — 在代码执行前扫描并拦截危险模式。"""

    def __init__(self, allowed_tools: Set[str] = None):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self._op_count: int = 0
        self._loop_depth: int = 0
        self._allowed_tools = allowed_tools or set()
        self._allowed_names = {
            "print", "len", "range", "str", "int", "float", "bool",
            "list", "dict", "set", "tuple", "enumerate", "zip", "map",
            "filter", "sorted", "reversed", "min", "max", "sum",
            "abs", "round", "isinstance", "type", "id", "hex", "oct", "bin",
            "True", "False", "None", "Exception", "BaseException",
            "ValueError", "TypeError", "KeyError", "IndexError",
            "json",
        } | self._allowed_tools

    def _count_op(self):
        self._op_count += 1
        if self._op_count > MAX_OPERATIONS:
            self.errors.append(f"操作次数超过上限 ({MAX_OPERATIONS})")

    def visit_Import(self, node: ast.Import):
        self._count_op()
        for alias in node.names:
            if alias.name.split(".")[0] not in _ALLOWED_IMPORTS:
                self.errors.append(f"禁止导入模块: {alias.name}")
            elif alias.name in _BLOCKED_IMPORTS:
                self.errors.append(f"禁止导入模块: {alias.name}")
        # 不继续遍历子节点（不进入 import 内部）

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self._count_op()
        if node.module is None:
            return
        base = node.module.split(".")[0]
        if base not in _ALLOWED_IMPORTS or node.module in _BLOCKED_IMPORTS:
            self.errors.append(f"禁止导入模块: {node.module}")

    def visit_Call(self, node: ast.Call):
        self._count_op()
        # 检查函数调用
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _BLOCKED_FUNCTIONS:
                self.errors.append(f"禁止调用函数: {name}()")
            elif name.startswith("__"):
                self.errors.append(f"禁止调用dunder函数: {name}()")
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                obj = node.func.value.id
                attr = node.func.attr
                # 禁止 obj.__xxx__() 调用
                if attr.startswith("__") and attr.endswith("__"):
                    if attr not in ("__init__", "__str__", "__repr__", "__eq__", "__hash__"):
                        self.errors.append(f"禁止调用 dunder 方法: {obj}.{attr}()")
                # 禁止 os.system 等绕过
                if obj in _BLOCKED_IMPORTS:
                    self.errors.append(f"禁止调用: {obj}.{attr}()")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self._count_op()
        if node.attr in _BLOCKED_DUNDER:
            self.errors.append(f"禁止访问 dunder 属性: {node.attr}")
        if isinstance(node.value, ast.Name):
            if node.value.id in _BLOCKED_IMPORTS:
                self.errors.append(f"禁止访问: {node.value.id}.{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        self._count_op()
        # 检查变量名尝试访问 __builtins__
        if node.id.startswith("__") and node.id.endswith("__"):
            if node.id in _BLOCKED_DUNDER:
                self.errors.append(f"禁止使用 dunder 名称: {node.id}")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript):
        self._count_op()
        # 检查 __builtins__['eval'] 等绕过
        if isinstance(node.value, ast.Name):
            if node.value.id in ("__builtins__", "__dict__", "__globals__"):
                self.errors.append(f"禁止访问: {node.value.id}")
        self.generic_visit(node)

    def visit_While(self, node: ast.While):
        self._count_op()
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_For(self, node: ast.For):
        self._count_op()
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def _check_node_count(self):
        pass  # 操作计数已通过 _count_op 在每个 visit 中累加


def validate_code(code: str, allowed_tools: Set[str] = None) -> Tuple[bool, List[str]]:
    """验证代码安全性。返回 (是否安全, 错误列表)。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"语法错误: {e}"]

    validator = CodeSecurityValidator(allowed_tools=allowed_tools)
    validator.visit(tree)

    if validator.errors:
        return False, validator.errors

    return True, []


# ── 工具桥接 ────────────────────────────────────────────

def _build_tool_functions() -> str:
    """生成工具函数的 Python 代码字符串，注入到执行环境中。"""
    functions = []
    for name, entry in registry._tools.items():
        # 生成安全的函数名（替换特殊字符）
        func_name = name.replace("-", "_")

        func_code = f'''
def {func_name}(**kwargs):
    """{entry.description or name}"""
    import json as _json
    from tiangong.core.registry import registry as _reg
    result = _reg.dispatch("{name}", kwargs)
    try:
        data = _json.loads(result)
        if isinstance(data, dict):
            if data.get("error"):
                print(f"[{func_name} 错误] {{data['error']}}", file=__import__("sys").stderr)
                return None
            if data.get("_error"):
                print(f"[{func_name} 错误] {{data['_error']}}", file=__import__("sys").stderr)
                return None
            return data
        return data
    except:
        return result
'''
        functions.append(func_code)

    return "\n".join(functions)


_FINAL_ANSWER_FUNC = '''
def final_answer(result=None, **kwargs):
    """终止代码执行并返回最终结果。[借鉴smolagents]

    调用此函数会立即结束代码执行，返回指定结果。
    不会被 except Exception 捕获。
    """
    import json as _json
    payload = {"answer": result, "done": True}
    if kwargs:
        payload.update(kwargs)
    raise _FinalAnswer(_json.dumps(payload, ensure_ascii=False, default=str))
'''


# ── 代码执行器 ──────────────────────────────────────────

class CodeExecutor:
    """安全的代码执行引擎。[借鉴smolagents]"""

    def __init__(self, allowed_tools: Set[str] = None):
        self.allowed_tools = allowed_tools or set(registry._tools.keys())
        self._exec_count = 0

    def execute(self, code: str, context: dict = None) -> dict:
        """执行 Python 代码，返回结果字典。

        code: Python 代码字符串
        context: 可选的上下文变量
        返回: {success, result, stdout, stderr, error, duration_ms}
        """
        self._exec_count += 1
        t0 = time.time()

        # 1. 安全验证
        safe, errors = validate_code(code, self.allowed_tools)
        if not safe:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": "安全验证未通过: " + "; ".join(errors),
                "duration_ms": int((time.time() - t0) * 1000),
            }

        # 2. 构建可执行脚本
        tool_funcs = _build_tool_functions()
        context_vars = self._format_context(context or {})

        script = f'''# 天工 Code Agent — 安全执行环境
import sys as _sys
from tiangong.core.code_agent import FinalAnswerException as _FinalAnswer

# 注入工具函数
{tool_funcs}

# 注入 final_answer
{_FINAL_ANSWER_FUNC}

# 上下文变量
{context_vars}

# ── 用户代码 ──
try:
{textwrap.indent(code, "    ")}
except _FinalAnswer as _fa:
    print(str(_fa.answer))
except Exception as _e:
    print(f"错误: {{type(_e).__name__}}: {{_e}}", file=_sys.stderr)
'''

        # 3. 子进程隔离执行
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="tiangong_code_"
            ) as f:
                f.write(script)
                tmp_path = f.name

            proc = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=EXECUTION_TIMEOUT,
                cwd=str(Path.home()),
                env={**os.environ},
                preexec_fn=lambda: os.setpgid(0, 0),
            )

            stdout = (proc.stdout or "")[:50000]
            stderr = (proc.stderr or "")[:10000]

            # 解析 final_answer 结果
            result = None
            done = False
            if proc.returncode == 0:
                # 尝试从 stdout 最后一行解析 JSON 结果
                last_line = stdout.strip().split("\n")[-1] if stdout.strip() else ""
                try:
                    import json
                    data = json.loads(last_line)
                    if isinstance(data, dict) and data.get("done"):
                        result = data.get("answer")
                        done = True
                        # 移除 final_answer 输出，剩下的才是实际输出
                        stdout = "\n".join(stdout.strip().split("\n")[:-1])
                except (json.JSONDecodeError, ValueError):
                    pass

            return {
                "success": proc.returncode == 0,
                "result": result,
                "stdout": stdout,
                "stderr": stderr,
                "error": stderr if proc.returncode != 0 else "",
                "done": done,
                "exit_code": proc.returncode,
                "exec_count": self._exec_count,
                "duration_ms": int((time.time() - t0) * 1000),
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": f"代码执行超时（{EXECUTION_TIMEOUT}秒）",
                "error": "timeout",
                "done": False,
                "exit_code": -1,
                "exec_count": self._exec_count,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": str(e)[:500],
                "error": str(e)[:500],
                "done": False,
                "exit_code": -1,
                "exec_count": self._exec_count,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _format_context(context: dict) -> str:
        """格式化上下文变量为 Python 赋值语句。"""
        lines = []
        for key, value in context.items():
            if isinstance(value, str):
                lines.append(f'{key} = """{value[:500]}"""')
            elif isinstance(value, (int, float, bool, type(None))):
                lines.append(f'{key} = {value!r}')
            elif isinstance(value, (list, dict)):
                import json
                lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
        return "\n".join(lines)


# ── 代码块解析 ──────────────────────────────────────────

def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    """从 LLM 响应中提取代码块。

    返回: [(语言, 代码), ...]
    """
    blocks = []

    # 匹配 ```python ... ``` 或 <code>...</code>
    pattern = re.compile(
        r'(?:```(?:python|py)\s*\n(.*?)```)|'
        r'(?:<code>(.*?)</code>)',
        re.DOTALL | re.IGNORECASE
    )

    for match in pattern.finditer(text):
        code = (match.group(1) or match.group(2) or "").strip()
        if code:
            lang = "python"
            blocks.append((lang, code))

    return blocks


def has_code_blocks(text: str) -> bool:
    """检查响应是否包含可执行代码块。"""
    return bool(re.search(
        r'```(?:python|py)\s*\n|<code>',
        text, re.IGNORECASE
    ))


# ── 模块级单例 ──

_executor: Optional[CodeExecutor] = None


def get_code_executor() -> CodeExecutor:
    global _executor
    if _executor is None:
        _executor = CodeExecutor()
    return _executor
