"""MCP (Model Context Protocol) 客户端 — 连接外部 MCP 服务器。[原创]

实现 MCP 协议的核心子集:
- stdio 传输: 子进程 JSON-RPC 通信
- 工具发现: tools/list → 自动注册到天工工具注册表
- 工具调用: tools/call → 转发到 MCP 服务器
- 多服务器: 同时连接多个 MCP 服务器

MCP 协议规范: https://modelcontextprotocol.io/
"""

import asyncio
import json
import logging
import subprocess
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# MCP 协议版本
MCP_VERSION = "2024-11-05"

# 连接超时
_CONNECT_TIMEOUT = 30.0
_REQUEST_TIMEOUT = 60.0


class MCPError(Exception):
    """MCP 协议错误。"""
    pass


class MCPConnection:
    """与一个 MCP 服务器的 stdio JSON-RPC 连接。"""

    def __init__(self, name: str, command: List[str], env: dict = None):
        self.name = name
        self.command = command
        self.env = env
        self.process: Optional[subprocess.Popen] = None
        self._tools: List[dict] = []
        self._next_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────

    def connect(self) -> bool:
        """启动 MCP 服务器子进程并完成初始化握手。"""
        try:
            import os
            env = {**os.environ}
            if self.env:
                env.update(self.env)

            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            self._running = True
            self._reader_thread = threading.Thread(
                target=self._read_loop, daemon=True
            )
            self._reader_thread.start()

            # 发送 initialize
            init_result = self._send_request("initialize", {
                "protocolVersion": MCP_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": "tiangong",
                    "version": "1.0.0",
                },
            })
            logger.info("MCP [%s] 初始化完成: %s", self.name,
                        init_result.get("serverInfo", {}).get("name", "未知"))

            # 发送 initialized 通知
            self._send_notification("notifications/initialized", {})

            return True
        except Exception as e:
            logger.error("MCP [%s] 连接失败: %s", self.name, e)
            self.disconnect()
            return False

    def disconnect(self):
        """断开连接并终止子进程。"""
        self._running = False
        if self.process:
            try:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            except Exception:
                pass
            self.process = None

    # ── 工具发现 ──────────────────────────────────────

    def list_tools(self) -> List[dict]:
        """获取 MCP 服务器提供的工具列表。"""
        if not self._tools:
            result = self._send_request("tools/list", {})
            self._tools = result.get("tools", [])
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 服务器的工具。"""
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        # MCP 工具返回 content 列表
        content = result.get("content", [])
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return "\n".join(text_parts)
        return json.dumps(result, ensure_ascii=False)

    # ── JSON-RPC 通信 ─────────────────────────────────

    def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        with self._lock:
            req_id = self._next_id
            self._next_id += 1

        future = asyncio.Future()
        self._pending[req_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        self._write(request)

        try:
            result = future.result(timeout=_REQUEST_TIMEOUT)
            if "error" in result:
                raise MCPError(
                    f"MCP 错误 [{method}]: {result['error'].get('message', str(result['error']))}"
                )
            return result.get("result", {})
        except TimeoutError:
            raise MCPError(f"MCP 请求超时: {method}")
        finally:
            self._pending.pop(req_id, None)

    def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无响应）。"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write(notification)

    def _write(self, data: dict):
        """写入一行 JSON 到子进程 stdin。"""
        if self.process and self.process.stdin:
            line = json.dumps(data, ensure_ascii=False) + "\n"
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise MCPError(f"写入失败: {e}")

    def _read_loop(self):
        """从子进程 stdout 循环读取 JSON-RPC 响应。"""
        if not self.process or not self.process.stdout:
            return
        try:
            for line in self.process.stdout:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("MCP [%s] 跳过非 JSON 行: %s", self.name, line[:100])
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    self._pending[req_id].set_result(msg)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            self._running = False


class MCPClient:
    """MCP 客户端 — 管理多个 MCP 服务器连接。"""

    def __init__(self):
        self._connections: Dict[str, MCPConnection] = {}
        self._tool_servers: Dict[str, str] = {}  # tool_name → server_name
        self._tool_handlers: Dict[str, Callable] = {}

    def add_server(self, name: str, command: List[str], env: dict = None) -> bool:
        """添加并连接一个 MCP 服务器。"""
        if name in self._connections:
            logger.warning("MCP 服务器已存在: %s", name)
            return False

        conn = MCPConnection(name, command, env)
        if not conn.connect():
            return False

        self._connections[name] = conn

        # 发现并注册工具
        tools = conn.list_tools()
        for tool in tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            self._tool_servers[tool_name] = name
            self._register_mcp_tool(name, conn, tool)

        logger.info("MCP [%s] 已注册 %d 个工具", name, len(tools))
        return True

    def remove_server(self, name: str):
        """断开并移除一个 MCP 服务器。"""
        conn = self._connections.pop(name, None)
        if conn:
            # 清理工具注册
            for tool_name, server_name in list(self._tool_servers.items()):
                if server_name == name:
                    self._tool_servers.pop(tool_name, None)
            conn.disconnect()

    def _register_mcp_tool(self, server_name: str, conn: MCPConnection, tool: dict):
        """将 MCP 工具注册到天工工具系统。"""
        from tiangong.core.registry import registry, tool_result, tool_error

        tool_name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {})

        # 构建天工兼容的 schema
        schema = {
            "name": f"mcp_{server_name}_{tool_name}",
            "description": f"[MCP:{server_name}] {description}",
            "parameters": {
                "type": "object",
                "properties": input_schema.get("properties", {}),
                "required": input_schema.get("required", []),
            },
        }

        def make_handler(srv_name: str, t_name: str, connection: MCPConnection):
            def handler(args: dict, **kwargs) -> str:
                try:
                    result_text = connection.call_tool(t_name, args)
                    return tool_result({"result": result_text})
                except MCPError as e:
                    return tool_error(str(e))
            return handler

        handler = make_handler(server_name, tool_name, conn)
        service_name = f"mcp_{server_name}_{tool_name}"
        self._tool_handlers[service_name] = handler

        registry.register(
            name=service_name,
            toolset=f"MCP:{server_name}",
            schema=schema,
            handler=handler,
            description=f"[MCP] {tool_name}: {description[:80]}",
            emoji="🔌",
            display_name=f"{tool_name} (MCP)",
        )

    def get_servers(self) -> List[dict]:
        """获取所有已连接服务器的状态。"""
        return [
            {
                "name": name,
                "command": " ".join(conn.command),
                "tools": len(conn._tools),
                "running": conn._running,
            }
            for name, conn in self._connections.items()
        ]

    def shutdown(self):
        """关闭所有 MCP 连接。"""
        for name in list(self._connections):
            self.remove_server(name)


# 模块级单例
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
