"""命令审批 — 危险命令模式检测 + 安全审批。[H]

借鉴 Hermes 的 Tirith 安全扫描和 dangerous command approval 模式。
"""

import logging
import re
from typing import Tuple, List

logger = logging.getLogger(__name__)

# 危险命令模式 [H]
DANGEROUS_PATTERNS = [
    (r"rm\s+-rf\s+/", "递归删除根目录"),
    (r"rm\s+-rf\s+~", "递归删除用户目录"),
    (r"rm\s+-rf\s+\$HOME", "递归删除用户目录"),
    (r"sudo\s+rm", "sudo删除操作"),
    (r"sudo\s+", "提权操作"),
    (r">\s*/dev/sd[a-z]", "直接写入磁盘设备"),
    (r"dd\s+if=", "磁盘直接操作"),
    (r"mkfs\.", "格式化文件系统"),
    (r"chmod\s+777", "过度权限设置"),
    (r"chmod\s+-R\s+777", "递归过度权限"),
    (r"chown\s+-R\s+", "递归更改所有者"),
    (r"curl.*\|\s*(ba)?sh", "管道执行远程脚本"),
    (r"wget.*\|\s*(ba)?sh", "管道执行远程脚本"),
    (r"curl.*\|\s*python", "管道执行Python脚本"),
    (r"git\s+push\s+--force", "强制推送"),
    (r"git\s+reset\s+--hard", "硬重置"),
    (r"shutdown", "系统关机"),
    (r"reboot", "系统重启"),
    (r"halt", "系统停机"),
    (r"killall", "批量杀进程"),
    (r"pkill\s+-9", "强制杀进程"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;:", "Fork炸弹"),
]

# 需要确认但非致命
WARNING_PATTERNS = [
    (r"pip\s+install", "pip安装包"),
    (r"pip3\s+install", "pip3安装包"),
    (r"npm\s+install\s+-g", "npm全局安装"),
    (r"brew\s+install", "Homebrew安装"),
    (r"gem\s+install", "gem安装"),
    (r"git\s+commit", "git提交"),
    (r"git\s+push", "git推送"),
    (r"docker\s+rm", "docker删除"),
    (r"docker\s+rmi", "docker镜像删除"),
    (r"launchctl\s+load", "launchctl加载服务"),
    (r"launchctl\s+unload", "launchctl卸载服务"),
]


class CommandApprover:
    """命令安全审批器。[H]"""

    def __init__(self, auto_approve_safe: bool = True):
        self.auto_approve_safe = auto_approve_safe
        self._session_allowlist: set = set()

    def check(self, command: str) -> Tuple[str, List[str]]:
        """检查命令安全性。

        返回: ("safe"|"dangerous"|"warning", 原因列表)
        """
        cmd_clean = command.strip()

        # 检查危险模式
        reasons = []
        for pattern, desc in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_clean):
                reasons.append(f"🚫 {desc}")

        if reasons:
            logger.warning("危险命令被拦截: %s — %s", cmd_clean[:100], "; ".join(reasons))
            return "dangerous", reasons

        # 检查警告模式
        for pattern, desc in WARNING_PATTERNS:
            if re.search(pattern, cmd_clean):
                reasons.append(f"⚠️ {desc}")

        if reasons:
            return "warning", reasons

        return "safe", []

    def approve(self, command: str) -> bool:
        """审批命令。危险命令返回 False。"""
        level, reasons = self.check(command)

        if level == "safe" and self.auto_approve_safe:
            return True

        if level == "dangerous":
            logger.warning("命令被拒绝: %s — %s", command[:100], reasons)
            return False

        # 警告级别 — 在CLI中会请求用户确认
        return True  # 默认允许，由上层处理确认

    def allowlist_add(self, pattern: str):
        """将模式加入本次会话的允许列表。"""
        self._session_allowlist.add(pattern)


# 模块级单例
approver = CommandApprover()
