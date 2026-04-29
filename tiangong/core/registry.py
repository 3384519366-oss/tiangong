"""Tool registry — singleton pattern adapted from Hermes.

Each tool file calls ``registry.register()`` at module level.
"""

import importlib
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ToolEntry:
    __slots__ = ("name", "toolset", "schema", "handler", "check_fn", "description", "emoji", "display_name")

    def __init__(self, name, toolset, schema, handler, check_fn=None, description="", emoji="", display_name=""):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.description = description or schema.get("description", "")
        self.emoji = emoji
        self.display_name = display_name or name


class ToolRegistry:
    """Singleton registry for tool schemas and handlers."""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._lock = threading.RLock()

    def register(self, name, toolset, schema, handler, check_fn=None, description="", emoji="", display_name=""):
        with self._lock:
            if name in self._tools:
                logger.warning("Tool %s already registered, skipping", name)
                return
            self._tools[name] = ToolEntry(
                name=name, toolset=toolset, schema=schema,
                handler=handler, check_fn=check_fn,
                description=description, emoji=emoji,
                display_name=display_name,
            )

    def get_schemas(self, tool_names: Set[str] | None = None) -> List[dict]:
        """Return OpenAI-format tool schemas, optionally filtered by name."""
        result = []
        entries = list(self._tools.values())
        for entry in entries:
            if tool_names and entry.name not in tool_names:
                continue
            if entry.check_fn:
                try:
                    if not entry.check_fn():
                        continue
                except Exception:
                    continue
            schema = {"type": "function", "function": {**entry.schema, "name": entry.name}}
            result.append(schema)
        return result

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Execute a tool handler. Returns JSON string."""
        entry = self._tools.get(name)
        if not entry:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            return entry.handler(args, **kwargs)
        except Exception as e:
            logger.exception("Tool %s dispatch error", name)
            return json.dumps({"error": f"Tool failed: {e}"})

    def get_all_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_display_names(self) -> List[str]:
        """返回所有工具的中文显示名称列表。"""
        return [entry.display_name for entry in self._tools.values()]

    def discover(self, tools_dir: Path):
        """Import all tool modules in tools_dir that register themselves."""
        for path in sorted(tools_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "registry.py":
                continue
            mod_name = f"tools.{path.stem}"
            try:
                # Adjust import for tiangong package
                importlib.import_module(f"tiangong.{mod_name}")
            except Exception as e:
                logger.warning("Could not import %s: %s", mod_name, e)


# Singleton
registry = ToolRegistry()


def tool_error(message, **extra) -> str:
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
