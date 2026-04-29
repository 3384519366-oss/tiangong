"""Computer Use orchestrator — vision-action loop.

Flow:
1. Capture screenshot
2. Vision model (Kimi K2.6) analyzes screen content
3. Model returns decision: click/type/scroll/etc with coordinates
4. Execute action via mouse/keyboard modules
5. Verify with another screenshot
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import screen as screen_mod
from . import mouse, keyboard
from . import accessibility
from .guard import guard
from tiangong.core.config import Config

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = """You are a computer vision agent running on macOS. You see a screenshot of the current screen and must decide what action to take.

Analyze the screenshot and return a JSON response with ONE action to perform:

Available actions:
- {"action": "click", "x": int, "y": int, "description": "what to click"}
- {"action": "double_click", "x": int, "y": int, "description": "what to double click"}
- {"action": "right_click", "x": int, "y": int, "description": "what"}
- {"action": "type", "text": "text to type", "description": "what to type"}
- {"action": "hotkey", "keys": ["cmd", "c"], "description": "shortcut"}
- {"action": "scroll", "direction": "up|down", "amount": int, "description": "scroll"}
- {"action": "drag", "from_x": int, "from_y": int, "to_x": int, "to_y": int, "description": "drag"}
- {"action": "wait", "seconds": int, "description": "why waiting"}
- {"action": "done", "result": "description of what was accomplished"}
- {"action": "error", "message": "description of what went wrong"}

Coordinates: the screenshot is a macOS screenshot. Estimate pixel coordinates from the image.
Be precise but conservative. If unsure about a click target, prefer to wait or report error.

Only return valid JSON, no other text."""


class ComputerOrchestrator:
    """Vision-action loop for computer use."""

    def __init__(self, vision_model: str = "kimi-k2.6"):
        self.vision_model = vision_model
        self.max_steps = 10
        self.step_delay = 0.5  # seconds between actions

    def _get_vision_client(self):
        """Get an LLM client configured for Kimi vision."""
        from tiangong.core.llm_client import LLMClient
        # Use Kimi K2.6 for vision capability
        return LLMClient(model_key=self.vision_model)

    def _analyze_screenshot(self, screenshot_path: Path, goal: str) -> dict:
        """Send screenshot to vision model for analysis."""
        client = self._get_vision_client()

        # Read and encode screenshot
        image_data = base64.b64encode(screenshot_path.read_bytes()).decode("utf-8")

        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Goal: {goal}\n\nAnalyze this screenshot and decide the next action:",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_data}"},
                    },
                ],
            },
        ]

        response = client.chat(messages)
        content = response.get("content", "").strip()

        # Parse JSON from response
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON object
            import re
            match = re.search(r'\{[^}]+\}', content)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return {"action": "error", "message": f"Failed to parse vision response: {content[:200]}"}

    def _execute_action(self, action: dict) -> str:
        """Execute a single action from the vision model."""
        action_type = action.get("action", "")

        if action_type == "click":
            x, y = int(action.get("x", 0)), int(action.get("y", 0))
            mouse.click(x, y)
            return f"Clicked at ({x}, {y})"

        elif action_type == "double_click":
            x, y = int(action.get("x", 0)), int(action.get("y", 0))
            mouse.double_click(x, y)
            return f"Double-clicked at ({x}, {y})"

        elif action_type == "right_click":
            x, y = int(action.get("x", 0)), int(action.get("y", 0))
            mouse.right_click(x, y)
            return f"Right-clicked at ({x}, {y})"

        elif action_type == "type":
            text = action.get("text", "")
            keyboard.type_text(text)
            return f"Typed: {text[:100]}"

        elif action_type == "hotkey":
            keys = action.get("keys", [])
            keyboard.hotkey(keys)
            return f"Pressed: {'+'.join(keys)}"

        elif action_type == "scroll":
            direction = action.get("direction", "down")
            amount = int(action.get("amount", 3))
            mouse.scroll(amount, direction)
            return f"Scrolled {direction} {amount} lines"

        elif action_type == "drag":
            mouse.drag(
                int(action.get("from_x", 0)), int(action.get("from_y", 0)),
                int(action.get("to_x", 0)), int(action.get("to_y", 0)),
            )
            return f"Dragged from ({action.get('from_x')},{action.get('from_y')}) to ({action.get('to_x')},{action.get('to_y')})"

        elif action_type == "wait":
            seconds = min(int(action.get("seconds", 1)), 10)
            time.sleep(seconds)
            return f"Waited {seconds}s"

        elif action_type in ("done", "error"):
            return action.get("result", action.get("message", "Done"))

        return f"Unknown action: {action_type}"

    def run(self, goal: str, max_steps: int = 10) -> Dict[str, Any]:
        """Execute a computer use task with vision guidance.

        Args:
            goal: Natural language description of what to do.
            max_steps: Maximum steps to take.

        Returns:
            Dict with result, steps_taken, and action_log.
        """
        steps_taken = 0
        action_log = []

        logger.info("Computer Use: goal=%s max_steps=%d", goal, max_steps)

        # Get current context
        app_info = accessibility.get_frontmost_app()

        for step in range(max_steps):
            logger.debug("Step %d/%d", step + 1, max_steps)

            # Capture screen
            screenshot = screen_mod.capture_screenshot()

            # Analyze with vision model
            action = self._analyze_screenshot(screenshot, goal)
            screenshot.unlink(missing_ok=True)

            action_type = action.get("action", "error")
            description = action.get("description", "")

            # Check safety
            allowed, reason = guard.check_operation(action_type, description)
            if not allowed:
                return {"success": False, "error": reason, "steps_taken": steps_taken, "log": action_log}

            if action_type == "done":
                action_log.append({"step": step, "action": action, "result": "Task completed"})
                return {
                    "success": True,
                    "result": action.get("result", "Done"),
                    "steps_taken": steps_taken + 1,
                    "log": action_log,
                }

            if action_type == "error":
                action_log.append({"step": step, "action": action, "result": "Error"})
                return {
                    "success": False,
                    "error": action.get("message", "Unknown error"),
                    "steps_taken": steps_taken,
                    "log": action_log,
                }

            # Execute
            result = self._execute_action(action)
            action_log.append({"step": step, "action": action, "result": result})
            logger.info("  Step %d: %s → %s", step + 1, action_type, result)
            steps_taken += 1

            time.sleep(self.step_delay)

        return {
            "success": False,
            "error": f"Max steps ({max_steps}) reached without completing task",
            "steps_taken": steps_taken,
            "log": action_log,
        }
