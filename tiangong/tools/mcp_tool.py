"""MCP 管理工具 — 管理 MCP 服务器连接。[原创]"""

from tiangong.core.registry import registry, tool_result, tool_error
from tiangong.core.mcp_client import get_mcp_client

MCP_SCHEMA = {
    "name": "mcp",
    "description": (
        "管理 MCP (Model Context Protocol) 服务器连接。"
        "可以添加、移除、列出 MCP 服务器，扩展工具能力。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作: add(添加服务器), remove(移除), list(列出), discover(重新发现工具)",
                "enum": ["add", "remove", "list", "discover"],
            },
            "server_name": {
                "type": "string",
                "description": "服务器名称（add/remove 时必填）。",
            },
            "command": {
                "type": "string",
                "description": "启动命令，如 'npx @anthropic/mcp-server-git'（add 时必填）。",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "命令参数列表。",
            },
        },
        "required": ["action"],
    },
}


def mcp_tool(args: dict, **kwargs) -> str:
    action = args.get("action", "")
    client = get_mcp_client()

    if action == "list":
        servers = client.get_servers()
        if not servers:
            return tool_result({"servers": [], "hint": "当前无 MCP 服务器连接。可在 config.yaml 配置 mcp.servers。"})
        return tool_result({"servers": servers})

    elif action == "add":
        server_name = args.get("server_name", "")
        command = args.get("command", "")
        cmd_args = args.get("args", [])

        if not server_name or not command:
            return tool_error("server_name 和 command 不能为空。")

        cmd_list = [command] + cmd_args
        ok = client.add_server(server_name, cmd_list)
        if ok:
            return tool_result({
                "added": True,
                "server": server_name,
                "tools_registered": len(client._tool_servers),
            })
        else:
            return tool_error(f"添加 MCP 服务器失败: {server_name}（可能已存在或启动失败）")

    elif action == "remove":
        server_name = args.get("server_name", "")
        if not server_name:
            return tool_error("server_name 不能为空。")
        client.remove_server(server_name)
        return tool_result({"removed": True, "server": server_name})

    elif action == "discover":
        # 重新发现所有已连接服务器的工具
        from tiangong.core.mcp_client import MCPConnection
        total = 0
        for name, conn in client._connections.items():
            conn._tools = []
            tools = conn.list_tools()
            for tool in tools:
                client._register_mcp_tool(name, conn, tool)
                total += 1
        return tool_result({"discovered": total})

    return tool_error(f"未知操作: {action}")


registry.register(
    name="mcp",
    toolset="核心",
    schema=MCP_SCHEMA,
    handler=mcp_tool,
    description="管理 MCP 服务器连接，扩展工具能力",
    emoji="🔌",
    display_name="MCP管理",
)
