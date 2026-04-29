"""Codebase 工具 — 代码库符号搜索 + 引用追踪 + 语义搜索。[原创]

提供三个查询接口:
- symbols: 按名称搜索函数/类/变量定义
- references: 查找符号的引用位置
- file: 查看文件中的所有符号结构
- search: 全文搜索代码库
- index: 触发重新索引
"""

import logging
from tiangong.core.registry import registry, tool_result, tool_error
from tiangong.core.code_indexer import get_indexer

logger = logging.getLogger(__name__)

CODEBASE_SCHEMA = {
    "name": "codebase",
    "description": (
        "代码库智能查询工具。支持:\n"
        "1. symbols: 按名称搜索符号（函数/类/变量/导入）定义位置\n"
        "2. references: 查找某个符号在代码库中的所有引用位置\n"
        "3. file: 查看指定文件中定义的所有符号结构\n"
        "4. search: 全文搜索代码库内容\n"
        "5. index: 重新索引代码库"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作类型",
                "enum": ["symbols", "references", "file", "search", "index", "stats"],
            },
            "query": {
                "type": "string",
                "description": "搜索查询（symbols/references/search 时使用）。",
            },
            "file_path": {
                "type": "string",
                "description": "文件路径（file 操作时使用）。",
            },
            "kind": {
                "type": "string",
                "description": "符号类型过滤: function, class, method, variable, import, constant, interface",
                "enum": ["function", "class", "method", "variable", "import", "constant", "interface", "type"],
            },
            "limit": {
                "type": "integer",
                "description": "最大返回数（默认 20）。",
            },
        },
        "required": ["action"],
    },
}


def codebase_tool(args: dict, **kwargs) -> str:
    action = args.get("action", "")
    query = args.get("query", args.get("symbol_name", ""))
    file_path = args.get("file_path", "")
    kind = args.get("kind", None)
    limit = min(args.get("limit", 20), 100)

    indexer = get_indexer()

    if action == "index":
        result = indexer.index(force=True)
        return tool_result({
            "indexed": True,
            **result,
        })

    elif action == "stats":
        return tool_result(indexer.get_stats())

    elif action == "symbols":
        if not query:
            return tool_error("symbols 操作需要 query 参数（符号名）。")
        # 自动索引（如果未索引）
        if indexer.stats["files"] == 0:
            indexer.index()
        results = indexer.find_symbols(query, kind=kind, limit=limit)
        return tool_result({
            "query": query,
            "kind": kind,
            "results": results,
            "count": len(results),
            "hint": "如需查看某符号的引用位置，使用 action=references" if results else "未找到匹配符号，可尝试 action=index 重建索引",
        })

    elif action == "references":
        if not query:
            return tool_error("references 操作需要 query 参数（符号名）。")
        if indexer.stats["files"] == 0:
            indexer.index()
        refs = indexer.find_references(query, limit=limit)
        # 也返回定义位置
        defs = indexer.find_symbols(query, limit=5)
        return tool_result({
            "symbol": query,
            "definitions": [{"file": d["file"], "line": d["line"], "kind": d["kind"]} for d in defs],
            "references": refs,
            "ref_count": len(refs),
        })

    elif action == "file":
        if not file_path:
            return tool_error("file 操作需要 file_path 参数。")
        if indexer.stats["files"] == 0:
            indexer.index()
        symbols = indexer.get_file_symbols(file_path)
        return tool_result({
            "file": file_path,
            "symbols": symbols,
            "count": len(symbols),
        })

    elif action == "search":
        if not query:
            return tool_error("search 操作需要 query 参数。")
        results = indexer.search_codebase(query, limit=limit)
        return tool_result({
            "query": query,
            "results": results,
            "count": len(results),
        })

    return tool_error(f"未知操作: {action}")


registry.register(
    name="codebase",
    toolset="核心",
    schema=CODEBASE_SCHEMA,
    handler=codebase_tool,
    description="代码库智能查询：符号定义、引用追踪、全文搜索",
    emoji="🏗️",
    display_name="代码库索引",
)
