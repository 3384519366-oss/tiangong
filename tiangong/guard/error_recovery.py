"""自动错误恢复 — 工具失败分析 + 智能重试 + 参数修正。[原创]

当工具返回错误时，自动分析错误原因并尝试修正重试。
- 错误分类器: 识别 12 种常见错误模式
- 修复建议器: 针对每种错误生成修正方案
- 重试策略: 最多 3 次，指数退避
- 上下文传递: 将修复历史注入 LLM
"""

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 最大重试次数
_MAX_RETRIES = 3
# 重试退避基值(秒)
_RETRY_BASE_DELAY = 0.5

# ── 错误模式库 ────────────────────────────────────────

# (模式, 分类, 修复建议)
_ERROR_PATTERNS: List[Tuple[str, str, str]] = [
    # 命令/程序
    (r"command not found[:\s]+(\S+)", "command_not_found",
     "命令 '{capture1}' 未找到。请检查是否已安装，或使用 brew install 安装。"),
    (r"zsh:\s*(\S+):\s*command not found", "command_not_found",
     "命令 '{capture1}' 未找到。尝试用 which/brew 查找或安装。"),
    (r"No such file or directory[:\s]+(.+)", "file_not_found",
     "文件 '{capture1}' 不存在。请检查路径是否正确。"),
    (r"cannot access ['\"]?(\S+)['\"]?", "file_not_found",
     "无法访问 '{capture1}'。检查文件是否存在及权限。"),

    # 权限
    (r"Permission denied", "permission_denied",
     "权限不足。如需系统级操作请告知用户手动执行。"),
    (r"Operation not permitted", "permission_denied",
     "操作不被允许。可能需要调整 macOS 隐私设置。"),
    (r"EACCES", "permission_denied",
     "权限不足 (EACCES)。请检查文件权限或使用 sudo（需用户确认）。"),

    # Python
    (r"ModuleNotFoundError[:\s]+(?:No module named\s+)?['\"]?(\S+)['\"]?", "import_error",
     "Python 模块 '{capture1}' 未安装。请用 pip install {capture1} 安装。"),
    (r"ImportError[:\s]+(.+)", "import_error",
     "导入错误: {capture1}。请检查模块名和依赖。"),
    (r"SyntaxError[:\s]+(.+)", "syntax_error",
     "Python 语法错误: {capture1}。请修正代码语法。"),
    (r"IndentationError", "syntax_error",
     "缩进错误。请检查代码缩进。"),

    # 网络
    (r"Connection refused", "network_error",
     "连接被拒绝。目标服务可能未运行。"),
    (r"Connection timed out", "network_error",
     "连接超时。请检查网络或增加超时时间。"),
    (r"Name or service not known", "network_error",
     "DNS 解析失败。请检查域名是否正确。"),
    (r"Could not resolve host", "network_error",
     "无法解析主机名。请检查网络连接。"),

    # 超时
    (r"timed?[ -]?out", "timeout",
     "操作超时。可以尝试简化操作或增加超时时间。"),

    # Git
    (r"fatal[:\s]+(.+)", "git_error",
     "Git 错误: {capture1}。"),
    (r"error[:\s]+(.+)", "git_error",
     "Git 错误: {capture1}。"),

    # 包管理
    (r"No formulae found", "brew_error",
     "Homebrew 未找到该软件包。请检查包名。"),
    (r"Error: No such keg", "brew_error",
     "该软件包未通过 Homebrew 安装。"),

    # 通用
    (r"(?:exit|return)[\s-]*code[:\s]*(\d+)", "exit_code",
     "命令退出码: {capture1}。"),
]

# 对每种错误分类的自动修复策略
_RECOVERY_STRATEGIES = {
    "command_not_found": {
        "action": "提示安装",
        "auto_fixable": False,
        "retry_adjustments": None,
    },
    "file_not_found": {
        "action": "修正路径",
        "auto_fixable": False,
        "retry_adjustments": ["使用绝对路径", "先 ls 确认父目录", "检查文件名拼写"],
    },
    "permission_denied": {
        "action": "跳过或提权",
        "auto_fixable": False,
        "retry_adjustments": ["尝试不加 sudo", "使用用户目录"],
    },
    "import_error": {
        "action": "pip install",
        "auto_fixable": False,
        "retry_adjustments": None,
    },
    "syntax_error": {
        "action": "修正语法",
        "auto_fixable": False,
        "retry_adjustments": ["检查括号匹配", "检查引号配对", "检查缩进"],
    },
    "network_error": {
        "action": "重试或切换",
        "auto_fixable": True,
        "retry_adjustments": ["增加超时时间", "重试连接", "换用 curl/wget"],
    },
    "timeout": {
        "action": "增加超时",
        "auto_fixable": True,
        "retry_adjustments": ["增加 timeout 参数", "简化命令", "分批执行"],
    },
    "git_error": {
        "action": "修正 git 操作",
        "auto_fixable": False,
        "retry_adjustments": None,
    },
    "brew_error": {
        "action": "检查包名",
        "auto_fixable": False,
        "retry_adjustments": ["修正包名", "brew search 查找", "使用 --cask"],
    },
    "exit_code": {
        "action": "分析退出码",
        "auto_fixable": False,
        "retry_adjustments": None,
    },
}


class ErrorReport:
    """错误分析报告。"""

    __slots__ = ("original_error", "category", "message",
                 "auto_fixable", "suggestion", "adjustments")

    def __init__(self):
        self.original_error: str = ""
        self.category: str = "unknown"
        self.message: str = ""
        self.auto_fixable: bool = False
        self.suggestion: str = ""
        self.adjustments: List[str] = []

    def to_prompt_hint(self) -> str:
        """生成注入 LLM 的错误提示。"""
        parts = [f"⚠️ 工具执行出错: {self.message}"]
        if self.suggestion:
            parts.append(f"建议: {self.suggestion}")
        if self.adjustments:
            parts.append("可尝试: " + "; ".join(self.adjustments))
        return "\n".join(parts)


def analyze_error(error_output: str) -> ErrorReport:
    """分析工具错误输出，返回 ErrorReport。"""
    report = ErrorReport()
    report.original_error = error_output[:2000]

    # 尝试从 JSON 中解析
    try:
        data = json.loads(error_output)
        if isinstance(data, dict):
            error_output = data.get("error", data.get("_error", data.get("stderr", error_output)))
            if isinstance(error_output, dict):
                error_output = error_output.get("message", str(error_output))
    except (json.JSONDecodeError, TypeError):
        pass

    error_str = str(error_output)

    # 匹配错误模式
    for pattern, category, template in _ERROR_PATTERNS:
        m = re.search(pattern, error_str, re.IGNORECASE)
        if m:
            report.category = category
            # 填充模板中的捕获组
            msg = template
            for i, group in enumerate(m.groups(), 1):
                msg = msg.replace(f"{{capture{i}}}", group or "")
            report.message = msg

            # 应用恢复策略
            strategy = _RECOVERY_STRATEGIES.get(category, {})
            report.auto_fixable = strategy.get("auto_fixable", False)
            report.suggestion = strategy.get("action", "")
            report.adjustments = list(strategy.get("retry_adjustments", []) or [])
            return report

    # 未匹配任何已知模式
    report.category = "unknown"
    report.message = error_str[:200]
    report.suggestion = "请分析错误原因并调整参数"
    return report


class RetryController:
    """重试控制器 — 管理每个工具调用的重试策略，支持指数退避。"""

    def __init__(self, max_retries: int = _MAX_RETRIES, base_delay: float = _RETRY_BASE_DELAY):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._retry_counts: Dict[str, int] = {}
        self._error_history: List[Dict[str, Any]] = []

    def should_retry(self, tool_name: str) -> bool:
        """检查是否应该重试。"""
        count = self._retry_counts.get(tool_name, 0)
        return count < self.max_retries

    def record_attempt(self, tool_name: str, args: dict,
                       error: str, report: ErrorReport):
        """记录一次尝试。"""
        self._retry_counts[tool_name] = self._retry_counts.get(tool_name, 0) + 1
        self._error_history.append({
            "tool": tool_name,
            "attempt": self._retry_counts[tool_name],
            "args": dict(args),
            "error": error[:500],
            "analysis": report.message,
            "suggestion": report.suggestion,
        })

    def get_retry_count(self, tool_name: str) -> int:
        return self._retry_counts.get(tool_name, 0)

    def get_context_for_llm(self) -> str:
        """生成错误历史上下文供 LLM 参考。"""
        if not self._error_history:
            return ""
        lines = ["## 最近工具执行错误"]
        for h in self._error_history[-5:]:
            lines.append(
                f"- [{h['tool']}]#{h['attempt']}: {h['analysis']}"
            )
        return "\n".join(lines)

    def delay(self, tool_name: str) -> float:
        """计算指数退避延迟。"""
        count = self._retry_counts.get(tool_name, 0)
        return self.base_delay * (2 ** count)

    def reset(self):
        self._retry_counts.clear()
        self._error_history.clear()


# ── Circuit Breaker 熔断器 [P1 补全] ────────────────────────────────

class CircuitBreaker:
    """熔断器 — 当工具连续失败超过阈值时，暂时屏蔽该工具，防止雪崩。

    状态机:
        CLOSED   -> 正常，允许调用
        OPEN     -> 熔断，拒绝调用，直接返回降级结果
        HALF_OPEN -> 试探，允许一次调用验证是否恢复
    """

    _STATE_CLOSED = "closed"
    _STATE_OPEN = "open"
    _STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = self._STATE_CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def can_execute(self) -> bool:
        """检查是否允许执行。"""
        with self._lock:
            if self._state == self._STATE_CLOSED:
                return True
            if self._state == self._STATE_OPEN:
                if self._should_attempt_reset():
                    self._state = self._STATE_HALF_OPEN
                    self._half_open_calls = 0
                    logger.info("熔断器进入半开放状态，尝试恢复")
                    return True
                return False
            if self._state == self._STATE_HALF_OPEN:
                return self._half_open_calls < self.half_open_max_calls
            return True

    def record_success(self):
        """记录一次成功调用。"""
        with self._lock:
            if self._state == self._STATE_HALF_OPEN:
                self._success_count += 1
                self._half_open_calls += 1
                if self._success_count >= self.half_open_max_calls:
                    self._reset()
                    logger.info("熔断器恢复正常 (CLOSED)")
            else:
                self._failure_count = 0
                self._success_count = 0

    def record_failure(self):
        """记录一次失败调用。"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == self._STATE_HALF_OPEN:
                self._state = self._STATE_OPEN
                logger.warning("熔断器重新打开 (OPEN)，半开放试探失败")
                return

            if self._failure_count >= self.failure_threshold:
                self._state = self._STATE_OPEN
                logger.warning(
                    "熔断器打开 (OPEN): 连续失败 %d 次，屏蔽 %d 秒",
                    self._failure_count, self.recovery_timeout,
                )

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return True
        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    def _reset(self):
        self._state = self._STATE_CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = None


class CircuitBreakerRegistry:
    """熔断器注册表 — 为每个工具维护独立的熔断器。"""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, tool_name: str) -> CircuitBreaker:
        with self._lock:
            if tool_name not in self._breakers:
                self._breakers[tool_name] = CircuitBreaker()
            return self._breakers[tool_name]

    def reset_all(self):
        with self._lock:
            for cb in self._breakers.values():
                cb._reset()
            self._breakers.clear()


# 全局熔断器注册表
_circuit_registry = CircuitBreakerRegistry()


def get_circuit_breaker(tool_name: str) -> CircuitBreaker:
    return _circuit_registry.get(tool_name)


# ── Fallback 降级策略 [P1 补全] ──────────────────────────────────

class FallbackStrategy:
    """降级策略 — 当工具/服务不可用时，提供替代方案。"""

    def __init__(self):
        self._fallbacks: Dict[str, callable] = {}

    def register(self, tool_name: str, fallback_fn: callable):
        """注册降级处理函数。"""
        self._fallbacks[tool_name] = fallback_fn

    def execute(self, tool_name: str, original_args: dict, original_error: str) -> str:
        """执行降级策略，返回降级结果。"""
        fallback_fn = self._fallbacks.get(tool_name)
        if fallback_fn:
            try:
                result = fallback_fn(original_args, original_error)
                logger.info("工具 %s 触发降级策略", tool_name)
                return json.dumps({
                    "success": True,
                    "fallback": True,
                    "result": result,
                    "note": f"原工具失败，已使用降级方案: {original_error[:200]}"
                }, ensure_ascii=False)
            except Exception as e:
                logger.warning("降级策略也失败了: %s", e)

        return json.dumps({
            "error": f"工具 {tool_name} 不可用 (熔断器开启): {original_error[:200]}"
        }, ensure_ascii=False)


_fallback_strategy = FallbackStrategy()


def register_fallback(tool_name: str, fallback_fn: callable):
    """注册工具的降级策略。"""
    _fallback_strategy.register(tool_name, fallback_fn)


# ── 增强版工具执行包装器 [P1 补全] ────────────────────────────

def wrap_with_recovery(
    tool_name: str,
    args: dict,
    handler_fn,
    retry_controller: RetryController = None,
) -> str:
    """包装工具执行，集成熔断器 + 自动重试 + 降级策略。

    执行流程:
    1. 检查熔断器状态
    2. 执行工具
    3. 分析错误、重试（可恢复错误）
    4. 记录成败/失败到熔断器
    5. 如果熔断器开启，返回降级结果
    """
    controller = retry_controller or _retry_controller
    breaker = get_circuit_breaker(tool_name)

    # 1. 检查熔断器
    if not breaker.can_execute():
        logger.warning("工具 %s 被熔断器屏蔽", tool_name)
        return _fallback_strategy.execute(tool_name, args, "熔断器开启")

    # 2. 执行工具
    result = handler_fn(args)

    # 3. 分析结果
    try:
        data = json.loads(result)
        is_error = bool(data.get("error") or data.get("_error"))
        if not is_error:
            breaker.record_success()
            return result
        error_text = data.get("error", data.get("_error", ""))
    except (json.JSONDecodeError, TypeError):
        breaker.record_success()
        return result

    # 4. 记录失败
    breaker.record_failure()

    # 5. 重试逻辑
    if not controller.should_retry(tool_name):
        return result

    report = analyze_error(error_text)

    if not report.auto_fixable:
        controller.record_attempt(tool_name, args, error_text, report)
        return result

    logger.info("自动重试 %s: %s (第%d次)",
                tool_name, report.message,
                controller.get_retry_count(tool_name) + 1)

    time.sleep(controller.delay(tool_name))
    controller.record_attempt(tool_name, args, error_text, report)

    adjusted_args = _adjust_args(tool_name, args, report)
    if adjusted_args != args:
        return handler_fn(adjusted_args)

    return handler_fn(args)


# ── 模块级重试控制器 ────────────────────────────────────────────────────────
_retry_controller = RetryController()


def get_retry_controller() -> RetryController:
    return _retry_controller


def _adjust_args(tool_name: str, args: dict, report: ErrorReport) -> dict:
    """根据错误报告调整工具参数。"""
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
