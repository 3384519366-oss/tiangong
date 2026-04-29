"""Write/Edit 工具 — 文件创建 + 精确字符串替换。[CC]

借鉴 Claude Code:
- Write: 创建或覆盖文件，原子写入 (tempfile + os.replace)
- Edit: 精确字符串替换，old_string 唯一性检查，replace_all 批量替换
"""

import logging
import os
import tempfile
from pathlib import Path

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

WRITE_SCHEMA = {
    "name": "write",
    "description": (
        "创建新文件或完全覆盖现有文件。使用原子写入确保数据安全。"
        "如果目标路径的父目录不存在，会自动创建。"
        "对于现有文件的局部修改，请优先使用 edit 工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要写入的文件绝对路径。",
            },
            "content": {
                "type": "string",
                "description": "要写入文件的完整内容。",
            },
        },
        "required": ["file_path", "content"],
    },
}

EDIT_SCHEMA = {
    "name": "edit",
    "description": (
        "精确替换文件中的指定字符串。可以替换单个匹配项或全部匹配项。"
        "old_string 必须在文件中唯一（或使用 replace_all）。"
        "适用于精确、安全地修改代码片段。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要编辑的文件绝对路径。",
            },
            "old_string": {
                "type": "string",
                "description": "要被替换的原始字符串，必须在文件中精确匹配。",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新字符串。",
            },
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有匹配项（默认 false，即要求唯一匹配）。",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}


def write_tool(args: dict, **kwargs) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")

    if not file_path:
        return tool_error("文件路径不能为空。")

    path = Path(file_path).expanduser().resolve()

    # 安全检查：禁止写入某些敏感位置
    home = str(Path.home())
    dangerous_prefixes = [
        "/etc/", "/System/", "/var/root/",
        f"{home}/.ssh/", f"{home}/.aws/", f"{home}/.gcp/",
        f"{home}/.bash_profile", f"{home}/.zshrc", f"{home}/.bashrc",
        f"{home}/.profile", f"{home}/.gitconfig", f"{home}/.netrc",
    ]
    for prefix in dangerous_prefixes:
        if str(path) == str(prefix.rstrip("/")) or str(path).startswith(str(prefix)):
            return tool_error(f"禁止写入敏感位置: {prefix}")

    # 创建父目录
    path.parent.mkdir(parents=True, exist_ok=True)

    # 原子写入: 临时文件 + os.replace
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.tmp."
        )
        try:
            os.write(tmp_fd, content.encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)

        os.replace(tmp_path, str(path))

        return tool_result({
            "written": True,
            "path": str(path),
            "size": len(content),
            "lines": content.count("\n") + 1,
        })
    except Exception as e:
        return tool_error(f"写入失败: {e}")


def edit_tool(args: dict, **kwargs) -> str:
    file_path = args.get("file_path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)

    if not file_path:
        return tool_error("文件路径不能为空。")
    if not old_string:
        return tool_error("old_string 不能为空。")

    path = Path(file_path).expanduser().resolve()

    # 安全检查：禁止编辑敏感位置
    home = str(Path.home())
    dangerous_prefixes = [
        "/etc/", "/System/", "/var/root/",
        f"{home}/.ssh", f"{home}/.aws", f"{home}/.gcp",
        f"{home}/.bash_profile", f"{home}/.zshrc", f"{home}/.bashrc",
        f"{home}/.profile", f"{home}/.gitconfig", f"{home}/.netrc",
    ]
    for prefix in dangerous_prefixes:
        if str(path).startswith(str(prefix)):
            return tool_error(f"禁止编辑敏感位置: {prefix}")

    if not path.exists():
        return tool_error(f"文件不存在: {path}")
    if path.is_dir():
        return tool_error(f"路径是目录而非文件: {path}")

    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return tool_error("无法解码文件（可能是二进制文件）。")

    count = original.count(old_string)
    if count == 0:
        return tool_error(
            f"在文件中未找到要替换的文本。请用 read 工具确认文件内容的精确格式（含缩进和空白字符）。"
        )
    if count > 1 and not replace_all:
        return tool_error(
            f"匹配到 {count} 处相同文本，必须指定 replace_all=true 才能全部替换。"
            f"如果只想替换其中一处，请提供更多上下文使 old_string 唯一。"
        )

    new_content = original.replace(old_string, new_string)

    # 原子写入
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.tmp."
        )
        try:
            os.write(tmp_fd, new_content.encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)

        os.replace(tmp_path, str(path))

        return tool_result({
            "edited": True,
            "path": str(path),
            "replacements": count,
            "old_length": len(old_string),
            "new_length": len(new_string),
        })
    except Exception as e:
        return tool_error(f"编辑失败: {e}")


registry.register(
    name="write",
    toolset="核心",
    schema=WRITE_SCHEMA,
    handler=write_tool,
    description="创建或覆盖文件，原子写入",
    emoji="✏️",
    display_name="写入文件",
)

registry.register(
    name="edit",
    toolset="核心",
    schema=EDIT_SCHEMA,
    handler=edit_tool,
    description="精确替换文件中的指定字符串",
    emoji="📝",
    display_name="编辑文件",
)
