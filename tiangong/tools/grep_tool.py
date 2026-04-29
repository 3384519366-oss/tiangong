"""Grep 工具 — 结构化代码搜索。[CC]

借鉴 Claude Code: 模式搜索 + 文件过滤 + 上下文行 + 结构化结果。
"""

import logging
import re
from pathlib import Path

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

GREP_SCHEMA = {
    "name": "grep",
    "description": (
        "在文件中搜索匹配指定模式的内容。支持正则表达式。"
        "返回匹配的文件路径、行号和内容片段。"
        "比裸 grep 命令更结构化，自动忽略 .git/node_modules/__pycache__ 等无关目录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要搜索的正则表达式模式。",
            },
            "path": {
                "type": "string",
                "description": "搜索的目录或文件路径（默认当前工作目录）。",
            },
            "glob": {
                "type": "string",
                "description": "文件过滤 glob 模式，如 '*.py' 或 '*.{ts,tsx}'。",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "是否忽略大小写（默认 false）。",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数（默认 50）。",
            },
            "context_lines": {
                "type": "integer",
                "description": "每个匹配项前后显示的上下文行数（默认 0）。",
            },
        },
        "required": ["pattern"],
    },
}

# 忽略的目录
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", ".next", ".nuxt", ".cache", ".idea", ".vscode",
    "target", "*.egg-info", ".mypy_cache", ".ruff_cache", ".pytest_cache",
}

# 忽略的文件前缀
_IGNORE_FILE_PREFIXES = {".", "~"}

# 默认文件扩展名（如果未指定 glob）
_DEFAULT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".yaml", ".yml",
    ".json", ".toml", ".cfg", ".ini", ".sh", ".bash", ".zsh",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp",
    ".html", ".css", ".scss", ".less", ".vue", ".svelte",
    ".sql", ".rb", ".php", ".swift", ".kt", ".scala",
    ".txt", ".csv", ".xml", ".tf", ".dockerfile", ".makefile",
}


def grep_tool(args: dict, **kwargs) -> str:
    pattern = args.get("pattern", "")
    search_path = args.get("path", str(Path.cwd()))
    glob_pattern = args.get("glob", "")
    ignore_case = args.get("ignore_case", False)
    max_results = min(args.get("max_results", 50), 200)
    context_lines = min(args.get("context_lines", 0), 5)

    if not pattern:
        return tool_error("搜索模式不能为空。")

    # 编译正则
    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as e:
        return tool_error(f"正则表达式错误: {e}")

    # 解析 glob
    exts = None
    if glob_pattern:
        # 简单 glob 解析: *.py, *.{ts,tsx}
        exts = set()
        m = re.match(r'\*\.\{([^}]+)\}', glob_pattern)
        if m:
            for ext in m.group(1).split(","):
                exts.add("." + ext.strip())
        elif glob_pattern.startswith("*."):
            exts.add(glob_pattern[1:])

    root = Path(search_path).expanduser().resolve()
    if not root.exists():
        return tool_error(f"路径不存在: {root}")

    results = []
    files_searched = 0

    if root.is_file():
        files = [root]
    else:
        files = _walk_files(root, exts or _DEFAULT_EXTS)

    for file_path in files:
        if len(results) >= max_results:
            break

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        files_searched += 1
        lines = content.split("\n")

        for line_idx, line_content in enumerate(lines):
            if len(results) >= max_results:
                break

            match = regex.search(line_content)
            if not match:
                continue

            entry = {
                "file": str(file_path),
                "line": line_idx + 1,
                "match": line_content.strip()[:200],
            }

            # 上下文行
            if context_lines > 0:
                ctx_start = max(0, line_idx - context_lines)
                ctx_end = min(len(lines), line_idx + context_lines + 1)
                entry["context"] = "\n".join(
                    f"{i + 1}: {l[:200]}"
                    for i, l in enumerate(lines[ctx_start:ctx_end], ctx_start)
                )

            results.append(entry)

    output = {
        "pattern": pattern,
        "results": results,
        "count": len(results),
        "files_searched": files_searched,
    }

    if len(results) >= max_results:
        output["hint"] = f"结果已达上限 ({max_results})，请缩小搜索范围或增加过滤条件。"

    return tool_result(output)


def _walk_files(root: Path, exts: set) -> list:
    """遍历目录，返回匹配的文件列表。"""
    files = []
    for item in root.rglob("*"):
        if item.is_dir():
            # 跳过忽略的目录
            if item.name in _IGNORE_DIRS:
                continue
            # 跳过隐藏目录
            if item.name.startswith("."):
                continue
        elif item.is_file():
            # 跳过隐藏文件
            if item.name.startswith("."):
                continue
            # 检查扩展名
            if item.suffix.lower() in exts or not item.suffix:
                files.append(item)
    return files


registry.register(
    name="grep",
    toolset="核心",
    schema=GREP_SCHEMA,
    handler=grep_tool,
    description="在文件中搜索匹配模式的内容，支持正则",
    emoji="🔍",
    display_name="代码搜索",
)
