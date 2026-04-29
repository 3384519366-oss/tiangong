"""Safety guardrails for Computer Use operations.

Blocks dangerous actions and requires user confirmation for sensitive ones.
"""

import logging
import re
from typing import Tuple

logger = logging.getLogger(__name__)

# Apps that can never be controlled
BLOCKED_APPS = [
    "Keychain Access",
    "1Password",
    "Bitwarden",
    "LastPass",
    "System Preferences",
    "System Settings",
    "Security & Privacy",
    "Terminal",  # CLI has its own bash tool, don't GUI it
    "Activity Monitor",  # Could kill system processes
]

# Operations that require user confirmation
SENSITIVE_PATTERNS = [
    (r"rm\s+-rf", "删除文件"),
    (r"sudo\s", "管理员权限操作"),
    (r"delete|remove|trash", "删除操作"),
    (r"payment|pay|checkout|购买|付款|支付", "支付操作"),
    (r"password|密码|passcode", "密码相关操作"),
    (r"sign\s*out|logout|退出登录|注销", "登出操作"),
    (r"uninstall|卸载", "卸载操作"),
]

# Operations that are always allowed (no confirmation needed)
SAFE_PATTERNS = [
    r"click|点击",
    r"scroll|滚动",
    r"type|输入",
    r"screenshot|截图",
    r"open|打开",
    r"search|搜索",
    r"read|阅读|查看",
    r"navigate|导航",
    r"close\s*(tab|window)?|关闭(标签|窗口)?",
]


class ComputerUseGuard:
    """Safety guard for computer use operations."""

    def __init__(self):
        self.operation_log: list = []
        self.require_confirmation = True
        self.blocked_windows: set = set()

    def check_app_allowed(self, app_name: str) -> Tuple[bool, str]:
        """Check if operation on an app is allowed."""
        for blocked in BLOCKED_APPS:
            if blocked.lower() in app_name.lower():
                return False, f"应用 {app_name} 已被阻止（敏感应用）"
        return True, ""

    def check_operation(self, action: str, target: str = "") -> Tuple[bool, str]:
        """Check if an operation is safe or needs confirmation.

        Returns:
            (allowed, reason) — if not allowed, reason explains why.
        """
        action_lower = action.lower()
        target_lower = target.lower()

        # Check app blocklist
        for blocked in BLOCKED_APPS:
            if blocked.lower() in target_lower:
                return False, f"目标应用 {target} 已被阻止"

        # Check sensitive patterns
        for pattern, desc in SENSITIVE_PATTERNS:
            if re.search(pattern, action_lower, re.IGNORECASE) or re.search(pattern, target_lower, re.IGNORECASE):
                if self.require_confirmation:
                    return True, f"需要确认: {desc}"
                logger.warning("Sensitive operation without confirmation: %s", desc)

        # Log the operation
        self.operation_log.append({
            "action": action,
            "target": target,
            "allowed": True,
        })

        return True, ""

    def log_operation(self, action: str, target: str, result: str):
        """Log an executed operation."""
        self.operation_log.append({
            "action": action, "target": target, "result": result,
        })

    def get_recent_operations(self, n: int = 10) -> list:
        """Get the last N operations."""
        return self.operation_log[-n:]


# Singleton
guard = ComputerUseGuard()
