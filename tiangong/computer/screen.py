"""Screen capture — macOS native via screencapture CLI + CGDisplay."""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def capture_screenshot(output_path: Optional[str] = None, quality: int = 80) -> Path:
    """Capture a screenshot using macOS screencapture command.

    Args:
        output_path: Where to save the PNG. If None, uses temp file.
        quality: JPEG quality if capturing as JPEG (1-100).

    Returns:
        Path to the captured screenshot.
    """
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".png", prefix="tiangong_screen_")
        output_path = Path(output_path)

    output_path = Path(output_path)

    # screencapture flags:
    # -x: no sound
    # -C: capture cursor
    # -t: format (png)
    cmd = ["screencapture", "-x", "-C", "-t", "png", str(output_path)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        logger.debug("Screenshot saved to %s", output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Screenshot failed: {e.stderr.decode() if e.stderr else e}")


def capture_screenshot_b64(quality: int = 80) -> str:
    """Capture screenshot and return as base64 string."""
    import base64

    path = capture_screenshot()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return base64.b64encode(data).decode("utf-8")


def capture_screenshot_to_temp() -> Path:
    """Capture and keep the temp file (caller should clean up)."""
    return capture_screenshot()


def get_screen_info() -> dict:
    """Get screen resolution and display info."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout

        # Parse resolution
        resolutions = []
        for line in output.split("\n"):
            line = line.strip()
            if "Resolution:" in line:
                resolutions.append(line.split(":")[-1].strip())

        return {
            "displays": len(resolutions),
            "resolutions": resolutions,
        }
    except Exception as e:
        return {"error": str(e)}


def get_mouse_position() -> Tuple[int, int]:
    """Get current mouse position using AppleScript."""
    try:
        script = 'tell application "System Events" to get the position of the mouse'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        # Returns "x, y"
        parts = result.stdout.strip().split(", ")
        return int(parts[0]), int(parts[1])
    except Exception:
        return (0, 0)


def get_frontmost_app() -> str:
    """Get the frontmost application name."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "Unknown"
