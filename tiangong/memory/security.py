"""记忆安全扫描 — 写入前检测注入/Unicode/凭据泄露。[CC]

借鉴 Claude Code 的 .memory_write.sh 安全扫描模式：
- 不可见 Unicode 字符检测
- 提示注入模式检测
- 凭据泄露模式检测
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# 不可见 Unicode 字符 [CC]
INVISIBLE_CHARS = [
    ("﻿", "BOM (零宽空格)"),
    ("​", "零宽空格"),
    ("‌", "零宽非连接符"),
    ("‍", "零宽连接符"),
    ("‎", "左至右标记"),
    ("‏", "右至左标记"),
    ("‪", "左至右嵌入"),
    ("‫", "右至左嵌入"),
    ("‬", "弹出方向格式"),
    ("‭", "左至右覆盖"),
    ("‮", "右至左覆盖"),
    ("⁠", "词连接符"),
    ("⁡", "数学不可见乘号"),
    ("⁢", "数学不可见分隔符"),
    ("⁣", "数学不可见分隔符2"),
    ("⁤", "不可见加号"),
    ("⁦", "左至右隔离"),
    ("⁧", "右至左隔离"),
    ("⁨", "首弱向隔离"),
    ("⁩", "弹出方向隔离"),
]

# 提示注入威胁模式 [CC]
INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|messages?)",
     "提示覆盖攻击"),
    (r"(?i)you\s+(are|now|act\s+as)\s+(an?\s+)?(unrestricted|unfiltered|evil|malicious|unethical|jailbroken)",
     "角色劫持攻击"),
    (r"(?i)(forget|disregard|override)\s+(your|all|previous)\s+(training|rules?|guidelines?|constraints?)",
     "规则绕过攻击"),
    (r"(?i)system\s*(:\s*|prompt\s*:|message\s*:|instruction\s*:)",
     "系统提示泄露"),
    (r"(?i)<\|im_start\|>|<\|im_end\|>",
     "标记注入"),
    (r"(?i)DAN\s*(mode\s*)?(prompt|jailbreak)",
     "DAN越狱攻击"),
]

# 凭据/密钥泄露模式 [CC]
CREDENTIAL_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey|secret|token|password|passwd)\s*[:=]\s*[\w\-]{8,}',
     "API密钥泄露"),
    (r'(?i)sk-[a-zA-Z0-9]{20,}',
     "OpenAI密钥格式"),
    (r'(?i)curl.*\$(\w*KEY\w*|\w*TOKEN\w*|\w*SECRET\w*)',
     "curl凭据泄露"),
    (r'(?i)cat\s+.*\.env',
     "环境变量文件读取"),
    (r'(?i)-----BEGIN\s+(RSA|OPENSSH|EC)\s+PRIVATE\s+KEY-----',
     "SSH私钥泄露"),
]


class MemorySecurityScanner:
    """记忆安全扫描器。"""

    @staticmethod
    def scan(content: str) -> Tuple[bool, List[str]]:
        """扫描内容的安全性。

        返回: (是否通过, 警告列表)
        """
        warnings = []

        # 1. 不可见 Unicode [CC]
        for char, name in INVISIBLE_CHARS:
            if char in content:
                warnings.append(f"检测到不可见Unicode: {name}")

        # 2. 提示注入模式 [CC]
        for pattern, desc in INJECTION_PATTERNS:
            if re.search(pattern, content):
                warnings.append(f"检测到注入威胁: {desc}")

        # 3. 凭据泄露 [CC]
        for pattern, desc in CREDENTIAL_PATTERNS:
            if re.search(pattern, content):
                warnings.append(f"检测到凭据泄露: {desc}")

        if warnings:
            logger.warning("Memory security scan found %d issue(s): %s",
                          len(warnings), "; ".join(warnings))
            return False, warnings

        return True, []

    @staticmethod
    def sanitize(content: str) -> str:
        """移除不可见 Unicode 字符。"""
        for char, _ in INVISIBLE_CHARS:
            content = content.replace(char, "")
        return content


# 模块级单例
scanner = MemorySecurityScanner()
