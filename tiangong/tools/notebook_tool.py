"""Notebook 工具 — Jupyter Notebook 读写与编辑。[CC]

借鉴 Claude Code: NotebookRead (读取) + NotebookEdit (编辑单元格)。
"""

import json
import logging
from pathlib import Path

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

NOTEBOOK_READ_SCHEMA = {
    "name": "notebook_read",
    "description": (
        "读取 Jupyter Notebook (.ipynb) 文件，展示所有单元格的源代码和输出。"
        "用于理解和审查 notebook 内容。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Notebook 文件的绝对路径。",
            },
        },
        "required": ["notebook_path"],
    },
}

NOTEBOOK_EDIT_SCHEMA = {
    "name": "notebook_edit",
    "description": (
        "编辑 Jupyter Notebook 的单元格内容。"
        "支持替换单元格源码(replace)、插入新单元格(insert)、删除单元格(delete)。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Notebook 文件的绝对路径。",
            },
            "cell_id": {
                "type": "string",
                "description": "目标单元格的 ID。插入模式下，新单元格插入到此 ID 之后；不指定则插入到开头。",
            },
            "new_source": {
                "type": "string",
                "description": "新的单元格源代码（replace/insert 时必填）。",
            },
            "cell_type": {
                "type": "string",
                "description": "单元格类型: code 或 markdown。",
                "enum": ["code", "markdown"],
            },
            "edit_mode": {
                "type": "string",
                "description": "编辑模式: replace(替换), insert(插入), delete(删除)。默认 replace。",
                "enum": ["replace", "insert", "delete"],
            },
        },
        "required": ["notebook_path"],
    },
}


def _read_notebook(path: Path) -> dict:
    """读取 notebook JSON。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Notebook JSON 解析失败: {e}")


def _save_notebook(path: Path, nb: dict):
    """写入 notebook JSON。"""
    path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")


def notebook_read_tool(args: dict, **kwargs) -> str:
    notebook_path = args.get("notebook_path", "")

    if not notebook_path:
        return tool_error("notebook_path 不能为空。")

    path = Path(notebook_path).expanduser().resolve()
    if not path.exists():
        return tool_error(f"文件不存在: {path}")
    if path.suffix.lower() != ".ipynb":
        return tool_error(f"不是 .ipynb 文件: {path}")

    try:
        nb = _read_notebook(path)

        cells_output = []
        for i, cell in enumerate(nb.get("cells", [])):
            cell_id = cell.get("id", f"cell_{i}")
            cell_type = cell.get("cell_type", "code")
            source = "".join(cell.get("source", []))

            cell_info = {
                "index": i,
                "id": cell_id,
                "type": cell_type,
                "source": source[:5000],
                "source_length": len(source),
            }

            # 包含输出
            outputs = []
            for out in cell.get("outputs", []):
                output_type = out.get("output_type", "")
                if output_type == "stream":
                    text = "".join(out.get("text", []))
                    outputs.append({"type": "stream", "name": out.get("name", "stdout"), "text": text[:2000]})
                elif output_type in ("execute_result", "display_data"):
                    data = out.get("data", {})
                    if "text/plain" in data:
                        outputs.append({"type": output_type, "text": "".join(data["text/plain"])[:2000]})
                elif output_type == "error":
                    outputs.append({
                        "type": "error",
                        "ename": out.get("ename", ""),
                        "evalue": out.get("evalue", "")[:1000],
                    })
            if outputs:
                cell_info["outputs"] = outputs

            # 执行计数
            exec_count = cell.get("execution_count")
            if exec_count is not None:
                cell_info["execution_count"] = exec_count

            cells_output.append(cell_info)

        return tool_result({
            "path": str(path),
            "nbformat": nb.get("nbformat"),
            "nbformat_minor": nb.get("nbformat_minor"),
            "cells": cells_output,
            "cell_count": len(cells_output),
            "metadata": nb.get("metadata", {}).get("kernelspec", {}),
        })

    except ValueError as e:
        return tool_error(str(e))
    except Exception as e:
        return tool_error(f"读取 notebook 失败: {e}")


def notebook_edit_tool(args: dict, **kwargs) -> str:
    notebook_path = args.get("notebook_path", "")
    cell_id = args.get("cell_id", "")
    new_source = args.get("new_source", "")
    cell_type = args.get("cell_type", "code")
    edit_mode = args.get("edit_mode", "replace")

    if not notebook_path:
        return tool_error("notebook_path 不能为空。")

    path = Path(notebook_path).expanduser().resolve()
    if not path.exists():
        return tool_error(f"文件不存在: {path}")
    if path.suffix.lower() != ".ipynb":
        return tool_error(f"不是 .ipynb 文件: {path}")

    try:
        nb = _read_notebook(path)
        cells = nb.setdefault("cells", [])

        if edit_mode == "delete":
            if not cell_id:
                return tool_error("delete 模式需要指定 cell_id。")
            target_idx = None
            for i, cell in enumerate(cells):
                if cell.get("id") == cell_id:
                    target_idx = i
                    break
            if target_idx is None:
                return tool_error(f"未找到单元格: {cell_id}")
            removed = cells.pop(target_idx)
            _save_notebook(path, nb)
            return tool_result({
                "deleted": True,
                "cell_id": cell_id,
                "cell_type": removed.get("cell_type", ""),
            })

        elif edit_mode == "insert":
            if not new_source:
                return tool_error("insert 模式需要指定 new_source。")
            import uuid
            new_cell = {
                "id": str(uuid.uuid4())[:8],
                "cell_type": cell_type,
                "source": new_source.splitlines(True),
                "metadata": {},
                "outputs": [] if cell_type == "code" else None,
                "execution_count": None,
            }

            if cell_id:
                # 插入到指定 cell 之后
                insert_idx = None
                for i, cell in enumerate(cells):
                    if cell.get("id") == cell_id:
                        insert_idx = i + 1
                        break
                if insert_idx is None:
                    return tool_error(f"未找到目标单元格: {cell_id}")
                cells.insert(insert_idx, new_cell)
            else:
                # 插入到开头
                cells.insert(0, new_cell)

            _save_notebook(path, nb)
            return tool_result({
                "inserted": True,
                "cell_id": new_cell["id"],
                "after": cell_id or "(开头)",
                "cell_type": cell_type,
            })

        else:  # replace
            if not cell_id:
                return tool_error("replace 模式需要指定 cell_id。")
            target = None
            for cell in cells:
                if cell.get("id") == cell_id:
                    target = cell
                    break
            if target is None:
                return tool_error(f"未找到单元格: {cell_id}")

            old_source = "".join(target.get("source", []))
            target["source"] = new_source.splitlines(True)
            if cell_type:
                target["cell_type"] = cell_type

            _save_notebook(path, nb)
            return tool_result({
                "replaced": True,
                "cell_id": cell_id,
                "old_length": len(old_source),
                "new_length": len(new_source),
            })

    except ValueError as e:
        return tool_error(str(e))
    except Exception as e:
        return tool_error(f"编辑 notebook 失败: {e}")


registry.register(
    name="notebook_read",
    toolset="核心",
    schema=NOTEBOOK_READ_SCHEMA,
    handler=notebook_read_tool,
    description="读取 Jupyter Notebook 文件",
    emoji="📓",
    display_name="读取Notebook",
)

registry.register(
    name="notebook_edit",
    toolset="核心",
    schema=NOTEBOOK_EDIT_SCHEMA,
    handler=notebook_edit_tool,
    description="编辑 Jupyter Notebook 单元格（替换/插入/删除）",
    emoji="📝",
    display_name="编辑Notebook",
)
