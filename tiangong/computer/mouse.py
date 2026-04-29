"""Mouse control via macOS CGEvent + AppleScript fallback."""

import logging
import subprocess
import time
from typing import Tuple

logger = logging.getLogger(__name__)


def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        logger.warning("AppleScript failed: %s", result.stderr.strip())
    return result.stdout.strip()


def move_to(x: int, y: int):
    """Move mouse to absolute coordinates."""
    # Use Core Graphics via Python for precise positioning
    try:
        import Quartz
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (x, y), 0))
    except ImportError:
        # Fallback: AppleScript / cliclick
        try:
            subprocess.run(["cliclick", f"m:{x},{y}"], capture_output=True, timeout=3)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Pure AppleScript fallback — less precise but works
            _run_applescript(f"""
                tell application "System Events"
                    set position of mouse to {{{x}, {y}}}
                end tell
            """)


def click(x: int | None = None, y: int | None = None, button: str = "left"):
    """Click at current position or move to (x,y) then click."""
    if x is not None and y is not None:
        move_to(x, y)
        time.sleep(0.1)

    try:
        import Quartz
        mouse_button = Quartz.kCGMouseButtonLeft if button == "left" else Quartz.kCGMouseButtonRight
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown if button == "left" else Quartz.kCGEventRightMouseDown, pos, mouse_button)
        up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp if button == "left" else Quartz.kCGEventRightMouseUp, pos, mouse_button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.05)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
    except ImportError:
        try:
            subprocess.run(["cliclick", f"c:{x},{y}" if x else "c:."], capture_output=True, timeout=3)
        except FileNotFoundError:
            _run_applescript(f"""
                tell application "System Events"
                    click at {{{x or 0}, {y or 0}}}
                end tell
            """)


def double_click(x: int | None = None, y: int | None = None):
    """Double click at position."""
    if x is not None and y is not None:
        move_to(x, y)
        time.sleep(0.1)
    try:
        import Quartz
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        for _ in range(2):
            down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pos, 0)
            up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pos, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            time.sleep(0.02)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.1)
    except ImportError:
        try:
            subprocess.run(["cliclick", f"dc:{x},{y}" if x else "dc:."], capture_output=True, timeout=3)
        except FileNotFoundError:
            click(x, y)
            time.sleep(0.3)
            click(x, y)


def right_click(x: int | None = None, y: int | None = None):
    """Right click at position."""
    click(x, y, button="right")


def drag(from_x: int, from_y: int, to_x: int, to_y: int, duration: float = 0.5):
    """Drag from (from_x, from_y) to (to_x, to_y)."""
    move_to(from_x, from_y)
    time.sleep(0.1)
    try:
        import Quartz
        mouse_down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (from_x, from_y), 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, mouse_down)
        steps = max(5, int(duration * 20))
        for i in range(1, steps + 1):
            ix = from_x + (to_x - from_x) * i // steps
            iy = from_y + (to_y - from_y) * i // steps
            move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, (ix, iy), 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
            time.sleep(duration / steps)
        mouse_up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, (to_x, to_y), 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, mouse_up)
    except ImportError:
        try:
            subprocess.run(["cliclick", f"dd:{from_x},{from_y}", f"du:{to_x},{to_y}"], capture_output=True, timeout=5)
        except FileNotFoundError:
            logger.warning("Drag not available without Quartz or cliclick")


def scroll(lines: int = 3, direction: str = "down"):
    """Scroll up or down."""
    delta = lines if direction == "down" else -lines
    try:
        import Quartz
        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, delta)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    except ImportError:
        _run_applescript(f"""
            tell application "System Events"
                repeat {abs(delta)} times
                    key code {"125" if direction == "down" else "126"}
                    delay 0.02
                end repeat
            end tell
        """)


def get_position() -> Tuple[int, int]:
    """Get current mouse position."""
    try:
        import Quartz
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (int(pos.x), int(pos.y))
    except ImportError:
        from .screen import get_mouse_position
        return get_mouse_position()
