"""技能工具 — Agent 按需加载和执行技能。[H+CC]"""

import json
import logging

from tiangong.core.registry import registry, tool_error, tool_result
from tiangong.skills.loader import get_skill_registry

logger = logging.getLogger(__name__)


def skill_tool_handler(args: dict, **kwargs) -> str:
    """处理技能操作。"""
    reg = get_skill_registry()
    action = args.get("action", "")

    if action == "list":
        skills = reg.list_all()
        if not skills:
            return tool_result({"skills": [], "message": "暂无可用技能。"})
        return tool_result({
            "skills": [{"name": s.name, "description": s.description,
                       "category": s.category} for s in skills],
            "count": len(skills),
        })

    elif action == "view":
        name = args.get("name", "")
        if not name:
            return tool_error("查看技能需要提供 name。")
        skill = reg.get(name)
        if not skill:
            return tool_error(f"技能 '{name}' 不存在。")

        file_path = args.get("file")
        if file_path:
            content = reg.get_tier3(name, file_path)
            if content is None:
                return tool_error(f"文件 '{file_path}' 不存在于技能 '{name}' 中。")
            return tool_result({"skill": name, "file": file_path, "content": content})

        return tool_result({
            "skill": name,
            "description": skill.description,
            "content": skill.tier2_content[:5000],
            "metadata": skill.metadata,
        })

    elif action == "search":
        query = args.get("query", "")
        if not query:
            return tool_error("搜索技能需要提供 query。")
        matches = []
        for skill in reg.list_all():
            if (query.lower() in skill.name.lower() or
                query.lower() in skill.description.lower() or
                query.lower() in skill.content.lower()):
                matches.append({
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                })
        return tool_result({"matches": matches, "count": len(matches)})

    else:
        return tool_error(f"未知操作: {action}。可用: list(列表), view(查看), search(搜索)。")


SKILL_SCHEMA = {
    "name": "skill",
    "description": (
        "查看和搜索可用技能。技能是预定义的特定领域知识和操作流程。"
        "操作: list(列出所有技能), view(查看技能详情), search(搜索技能)。"
        "当需要特定领域专业知识或操作流程时使用此工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "view", "search"],
                "description": "操作: list(列表), view(查看), search(搜索)。"
            },
            "name": {
                "type": "string",
                "description": "技能名称（view 操作需要）。"
            },
            "file": {
                "type": "string",
                "description": "技能关联文件路径（view 操作可选，如 'references/guide.md'）。"
            },
            "query": {
                "type": "string",
                "description": "搜索关键词（search 操作需要）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="skill",
    toolset="技能",
    schema=SKILL_SCHEMA,
    handler=skill_tool_handler,
    description="查看和搜索可用技能，获取领域专业知识",
    emoji="📚",
    display_name="技能",
)
