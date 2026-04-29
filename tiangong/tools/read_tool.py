"""Read 工具 — 文件读取 + 图片/PDF 识别。[CC]

借鉴 Claude Code: 带行号读取、分页支持、自动识别图片和 PDF。
"""

import logging
from pathlib import Path

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

READ_SCHEMA = {
    "name": "read",
    "description": (
        "读取文件内容，带行号显示。支持文本文件、图片（PNG/JPG/GIF/BMP/WebP）和 PDF 文件。"
        "可以用 offset 和 limit 控制读取范围，对长文件分页读取。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件绝对路径。",
            },
            "offset": {
                "type": "integer",
                "description": "从第几行开始读取（1-based，默认 1）。仅对文本文件有效。",
            },
            "limit": {
                "type": "integer",
                "description": "最多读取多少行（默认 500）。仅对文本文件有效。",
            },
            "pages": {
                "type": "string",
                "description": "PDF 页面范围，如 '1-5' 或 '3' 或 '1,3,5'。仅对 PDF 文件有效。",
            },
        },
        "required": ["file_path"],
    },
}

# 图片扩展名
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".tif"}
_PDF_EXT = ".pdf"

# 最大文本文件大小 (10MB)
_MAX_TEXT_SIZE = 10 * 1024 * 1024
_DEFAULT_LIMIT = 500


def read_tool(args: dict, **kwargs) -> str:
    file_path = args.get("file_path", "")
    offset = max(1, args.get("offset", 1))
    limit = min(args.get("limit", _DEFAULT_LIMIT), 2000)
    pages = args.get("pages", "")

    if not file_path:
        return tool_error("文件路径不能为空。")

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return tool_error(f"文件不存在: {path}")

    if path.is_dir():
        return tool_error(f"路径是目录而非文件: {path}。如需列出目录，请使用 bash 工具执行 ls。")

    ext = path.suffix.lower()

    # ── 图片 ──
    if ext in _IMAGE_EXTS:
        try:
            from PIL import Image
            img = Image.open(path)
            info = {
                "type": "image",
                "path": str(path),
                "format": img.format,
                "size": f"{img.width}x{img.height}",
                "mode": img.mode,
                "file_size": path.stat().st_size,
            }
            # 尝试 OCR 提取文字
            try:
                import subprocess
                r = subprocess.run(
                    ["shortcuts", "run", "Extract Text from Image",
                     "-i", str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                if r.stdout.strip():
                    info["ocr_text"] = r.stdout.strip()[:5000]
            except Exception:
                pass
            return tool_result(info)
        except ImportError:
            # 没有 PIL，返回基本信息
            return tool_result({
                "type": "image",
                "path": str(path),
                "file_size": path.stat().st_size,
                "hint": "图片内容可通过系统预览查看。安装 Pillow 可获取更多信息。",
            })

    # ── PDF ──
    if ext == _PDF_EXT:
        try:
            import subprocess
            # 用 Python 内置或系统的 pdftotext
            cmd = ["pdftotext", "-layout"]
            if pages:
                # pdftotext 用 -f 和 -l 指定页码范围
                ps = pages.replace(",", " ").split()
                first = ps[0].split("-")[0] if "-" in ps[0] else ps[0]
                last = ps[-1].split("-")[-1] if "-" in ps[-1] else ps[-1]
                cmd += ["-f", first, "-l", last]
            cmd += [str(path), "-"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            text = r.stdout or ""
            if not text:
                # 尝试用 Python 的 PyPDF2
                try:
                    from PyPDF2 import PdfReader
                    reader = PdfReader(str(path))
                    page_range = _parse_page_range(pages, len(reader.pages))
                    lines = []
                    for i in page_range:
                        if i < len(reader.pages):
                            page_text = reader.pages[i].extract_text() or ""
                            lines.append(f"── 第 {i + 1} 页 ──\n{page_text}")
                    text = "\n\n".join(lines)
                except ImportError:
                    return tool_result({
                        "type": "pdf",
                        "path": str(path),
                        "pages": len(reader.pages) if 'reader' in dir() else "?",
                        "hint": "安装 pdftotext (brew install poppler) 或 PyPDF2 可提取文字。",
                    })
            # 截断
            max_chars = 25000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... (已截断，全文 {len(text)} 字符)"
            return tool_result({
                "type": "pdf",
                "path": str(path),
                "content": text,
            })
        except Exception as e:
            return tool_result({
                "type": "pdf",
                "path": str(path),
                "file_size": path.stat().st_size,
                "hint": f"无法提取 PDF 文字: {e}。安装 pdftotext (brew install poppler) 或 PyPDF2。",
            })

    # ── 文本文件 ──
    if path.stat().st_size > _MAX_TEXT_SIZE:
        return tool_error(f"文件过大 ({path.stat().st_size / 1024 / 1024:.1f}MB)，超过 {_MAX_TEXT_SIZE / 1024 / 1024:.0f}MB 限制。请用 offset/limit 分页读取。")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_text(encoding="latin-1")
        except Exception as e:
            return tool_error(f"无法解码文件: {e}。可能是二进制文件，请用其他工具处理。")

    lines = content.split("\n")
    total_lines = len(lines)

    # 分页
    start = offset - 1
    end = min(start + limit, total_lines)
    selected = lines[start:end]

    # 带行号格式化
    line_width = len(str(end))
    formatted = []
    for i, line_content in enumerate(selected, start=start + 1):
        formatted.append(f"{i:>{line_width}} │ {line_content}")

    output = "\n".join(formatted)

    # 截断过长行
    max_output = 50000
    if len(output) > max_output:
        output = output[:max_output] + f"\n... (已截断，全文 {len(output)} 字符)"

    result = {
        "type": "text",
        "path": str(path),
        "total_lines": total_lines,
        "shown_lines": f"{start + 1}-{end}",
        "content": output,
    }
    if end < total_lines:
        result["hint"] = f"还有 {total_lines - end} 行未显示，增加 offset={end + 1} 继续读取。"

    return tool_result(result)


def _parse_page_range(pages: str, total: int) -> list:
    """解析页面范围字符串，返回 0-based 页面索引列表。"""
    if not pages:
        return list(range(total))
    indices = set()
    for part in pages.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                indices.update(range(int(a) - 1, int(b)))
            except ValueError:
                pass
        else:
            try:
                indices.add(int(part) - 1)
            except ValueError:
                pass
    return sorted(i for i in indices if 0 <= i < total)


registry.register(
    name="read",
    toolset="核心",
    schema=READ_SCHEMA,
    handler=read_tool,
    description="读取文件内容（文本/图片/PDF），带行号显示",
    emoji="📖",
    display_name="读取文件",
)
