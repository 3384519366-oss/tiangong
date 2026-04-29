"""Accessibility API — read UI elements via AppleScript System Events."""

import json
import logging
import subprocess

logger = logging.getLogger(__name__)


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        logger.warning("osascript error: %s", result.stderr.strip()[:200])
    return result.stdout.strip()


def get_frontmost_app() -> dict:
    """Get details about the frontmost application."""
    script = """
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        set appName to name of frontApp
        set appTitle to ""
        try
            set appTitle to name of front window of frontApp
        end try
        return appName & "||" & appTitle
    end tell
    """
    result = _osascript(script)
    parts = result.split("||", 1)
    return {
        "name": parts[0] if parts else "Unknown",
        "title": parts[1] if len(parts) > 1 else "",
    }


def get_window_bounds() -> dict:
    """Get front window position and size."""
    script = """
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        tell front window of frontApp
            set wPos to position
            set wSize to size
            return (item 1 of wPos) & "," & (item 2 of wPos) & "," & (item 1 of wSize) & "," & (item 2 of wSize)
        end tell
    end tell
    """
    result = _osascript(script)
    try:
        parts = [int(p) for p in result.split(",")]
        return {"x": parts[0], "y": parts[1], "width": parts[2], "height": parts[3]}
    except (ValueError, IndexError):
        return {}


def get_ui_elements() -> list:
    """Get a list of interactive UI elements in the front window."""
    script = """
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        tell front window of frontApp
            set elemList to {}
            try
                set uiElems to every UI element
                repeat with e in uiElems
                    set end of elemList to (name of e & "|" & (role of e) & "|" & (description of e))
                end repeat
            end try
            return elemList as string
        end tell
    end tell
    """
    result = _osascript(script)
    if not result:
        return []

    elements = []
    for line in result.split(", "):
        parts = line.split("|")
        if len(parts) >= 2:
            elements.append({
                "name": parts[0], "role": parts[1],
                "description": parts[2] if len(parts) > 2 else "",
            })
    return elements


def get_ui_tree(max_depth: int = 3) -> list:
    """Get UI element hierarchy."""
    script = f"""
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        return my getTree(front window of frontApp, {max_depth})
    end tell

    on getTree(parent, depth)
        if depth <= 0 then return {{}}
        set output to {{}}
        try
            set children to every UI element of parent
            repeat with child in children
                set childName to ""
                set childRole to ""
                try
                    set childName to name of child
                end try
                try
                    set childRole to role of child
                end try
                set end of output to childRole & ":" & childName
            end repeat
        end try
        return output as string
    end getTree
    """
    result = _osascript(script)
    return [e.strip() for e in result.split(", ") if e.strip()][:30]


def click_element(name: str, role: str = "button") -> bool:
    """Click a UI element by name and role."""
    script = f"""
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        tell front window of frontApp
            try
                click (first {role} whose name is "{name}")
                return "ok"
            on error errMsg
                return errMsg
            end try
        end tell
    end tell
    """
    result = _osascript(script)
    return result == "ok"


def get_menu_items() -> list:
    """Get menu bar items for current app."""
    script = """
    tell application "System Events"
        set frontApp to first process whose frontmost is true
        set menuNames to {}
        try
            set menuBars to menu bars of frontApp
            repeat with mb in menuBars
                set menusList to menus of mb
                repeat with m in menusList
                    set end of menuNames to name of m
                end repeat
            end repeat
        end try
        return menuNames as string
    end tell
    """
    result = _osascript(script)
    return [m.strip() for m in result.split(", ") if m.strip()]
