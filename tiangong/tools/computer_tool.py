"""屏幕操控工具 — 让 agent 能看屏、控屏、操作 GUI 应用。

面向 agent 的工具接口，视觉-操作循环在 orchestrator.py 中。
"""

import json
import logging

from tiangong.core.registry import registry, tool_error, tool_result
from tiangong.computer import screen, mouse, keyboard, accessibility
from tiangong.computer.guard import guard
from tiangong.computer.orchestrator import ComputerOrchestrator

logger = logging.getLogger(__name__)


def computer_tool_handler(args: dict, **kwargs) -> str:
    """分发屏幕操控动作。"""
    action = args.get("action", "")

    # ── 只读操作（始终安全）──
    if action == "screenshot":
        try:
            path = screen.capture_screenshot()
            info = screen.get_screen_info()
            return json.dumps({
                "success": True,
                "file": str(path),
                "size_bytes": path.stat().st_size,
                "screen": info,
            }, ensure_ascii=False)
        except Exception as e:
            return tool_error(f"截图失败: {e}")

    elif action == "screen_info":
        return tool_result(screen.get_screen_info())

    elif action == "mouse_position":
        return tool_result({"position": mouse.get_position()})

    elif action == "frontmost_app":
        return tool_result(accessibility.get_frontmost_app())

    elif action == "window_bounds":
        return tool_result(accessibility.get_window_bounds())

    # ── GUI 操作 ──
    elif action == "click":
        x = args.get("x")
        y = args.get("y")
        if x is not None and y is not None:
            mouse.click(int(x), int(y))
        else:
            mouse.click()
        return tool_result({"clicked": f"({x}, {y})" if x else "当前位置"})

    elif action == "double_click":
        mouse.double_click(args.get("x"), args.get("y"))
        return tool_result("双击完成")

    elif action == "right_click":
        mouse.right_click(args.get("x"), args.get("y"))
        return tool_result("右键点击完成")

    elif action == "move_mouse":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        mouse.move_to(x, y)
        return tool_result({"moved_to": (x, y)})

    elif action == "type":
        text = args.get("text", "")
        if not text:
            return tool_error("输入操作需要提供 text 参数。")
        keyboard.type_text(str(text))
        return tool_result({"typed": text[:100]})

    elif action == "hotkey":
        keys = args.get("keys", [])
        if not keys:
            return tool_error("需要提供 keys 列表。")
        keyboard.hotkey(keys)
        return tool_result({"hotkey": "+".join(keys)})

    elif action == "scroll":
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3))
        mouse.scroll(amount, direction)
        return tool_result(f"scrolled_{direction}")

    elif action == "press_key":
        key = args.get("key", "")
        if not key:
            return tool_error("需要提供 key 名称。")
        keyboard.press_key(key)
        return tool_result({"pressed": key})

    # ── 剪贴板 ──
    elif action == "copy":
        keyboard.copy()
        return tool_result("已复制")

    elif action == "paste":
        keyboard.paste()
        return tool_result("已粘贴")

    # ── 无障碍访问 ──
    elif action == "get_ui_elements":
        return tool_result({"elements": accessibility.get_ui_elements()[:20]})

    elif action == "click_element":
        name = args.get("name", "")
        role = args.get("role", "button")
        if not name:
            return tool_error("需要提供 UI 元素 name。")
        ok = accessibility.click_element(name, role)
        return tool_result({"clicked": ok})

    # ── 视觉任务编排 ──
    elif action == "vision_task":
        goal = args.get("goal", "")
        if not goal:
            return tool_error("视觉任务需要提供 goal。")
        max_steps = int(args.get("max_steps", 10))
        orch = ComputerOrchestrator()
        result = orch.run(goal, max_steps=max_steps)
        return json.dumps(result, ensure_ascii=False)

    else:
        return tool_error(f"未知操作: {action}。可用: screenshot, click, type, hotkey, scroll, vision_task 等。")


COMPUTER_SCHEMA = {
    "name": "computer",
    "description": (
        "操控 macOS 电脑 — 查看屏幕、点击、输入、滚动、与 GUI 应用交互。"
        "当需要操作没有命令行替代方案的图形界面应用时使用此工具。"
        "\n\n可用操作:\n"
        "- screenshot: 截取当前屏幕\n"
        "- click/double_click/right_click: 在 (x,y) 或当前位置点击\n"
        "- move_mouse: 移动鼠标到 (x,y)\n"
        "- type: 通过键盘输入文本\n"
        "- hotkey: 按下组合键（如 ['cmd','c']）\n"
        "- scroll: 上下滚动\n"
        "- press_key: 按下单个按键\n"
        "- copy/paste: 剪贴板操作\n"
        "- get_ui_elements: 列出前台窗口的 UI 元素\n"
        "- click_element: 按名称点击 UI 元素\n"
        "- frontmost_app: 获取当前应用信息\n"
        "- mouse_position: 获取光标位置\n"
        "- vision_task: 多步骤视觉引导任务（截图→AI分析→执行操作）"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "screenshot", "screen_info", "mouse_position", "frontmost_app",
                    "window_bounds", "click", "double_click", "right_click",
                    "move_mouse", "type", "hotkey", "scroll", "press_key",
                    "copy", "paste", "get_ui_elements", "click_element", "vision_task",
                ],
                "description": "要执行的屏幕操控动作。"
            },
            "x": {"type": "number", "description": "X 坐标（像素）。"},
            "y": {"type": "number", "description": "Y 坐标（像素）。"},
            "text": {"type": "string", "description": "要输入的文本。"},
            "keys": {"type": "array", "items": {"type": "string"}, "description": "组合键列表（最后一个为主键，前面的为修饰键）。"},
            "key": {"type": "string", "description": "单个按键名称。"},
            "direction": {"type": "string", "enum": ["up", "down"], "description": "滚动方向: up(上) / down(下)。"},
            "amount": {"type": "integer", "description": "滚动量（行数）或最大步骤数。"},
            "name": {"type": "string", "description": "要交互的 UI 元素名称。"},
            "role": {"type": "string", "description": "UI 元素角色（button, menu 等）。"},
            "goal": {"type": "string", "description": "视觉任务的自然语言目标描述。"},
            "max_steps": {"type": "integer", "description": "视觉任务最大步骤数（默认 10）。"},
        },
        "required": ["action"],
    },
}

registry.register(
    name="computer",
    toolset="屏幕操控",
    schema=COMPUTER_SCHEMA,
    handler=computer_tool_handler,
    description="操控 macOS GUI —— 看屏幕、点鼠标、敲键盘、滚动",
    emoji="🖥️",
    display_name="屏幕操控",
)
