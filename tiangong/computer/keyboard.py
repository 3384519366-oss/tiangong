"""Keyboard control via macOS CGEvent + AppleScript fallback."""

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

# macOS key codes
KEY_CODES = {
    "return": 36, "enter": 76, "tab": 48, "space": 49,
    "delete": 51, "escape": 53, "command": 55, "cmd": 55,
    "shift": 56, "caps_lock": 57, "option": 58, "alt": 58,
    "control": 59, "ctrl": 59, "right_shift": 60,
    "right_option": 61, "right_control": 62,
    "fn": 63, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5,
    "h": 4, "i": 34, "j": 38, "k": 40, "l": 37, "m": 46,
    "n": 45, "o": 31, "p": 35, "q": 12, "r": 15, "s": 1,
    "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
}


def _run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        logger.warning("AppleScript error: %s", result.stderr.strip())
    return result.stdout.strip()


def _key_event(key_code: int, key_down: bool, flags: int = 0):
    """Send a key event via Core Graphics."""
    try:
        import Quartz
        event = Quartz.CGEventCreateKeyboardEvent(None, key_code, key_down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    except ImportError:
        pass  # Will fallback in higher-level functions


def type_text(text: str, delay: float = 0.01):
    """Type text using AppleScript keystroke (handles Unicode)."""
    # Escape special chars for AppleScript
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    _run_applescript(f'tell application "System Events" to keystroke "{escaped}"')


def press_key(key: str, modifiers: list = None):
    """Press a single key, optionally with modifiers."""
    try:
        import Quartz
        flags = 0
        mod_map = {"cmd": 0x100000, "command": 0x100000, "shift": 0x20000,
                    "option": 0x80000, "alt": 0x80000, "control": 0x40000, "ctrl": 0x40000}
        if modifiers:
            for m in modifiers:
                flags |= mod_map.get(m.lower(), 0)

        key_lower = key.lower()
        key_code = KEY_CODES.get(key_lower)
        if key_code is None:
            # Fallback: use AppleScript keystroke for unknown keys
            _run_applescript(f'tell application "System Events" to keystroke "{key}"')
            return

        _key_event(key_code, True, flags)
        time.sleep(0.02)
        _key_event(key_code, False, flags)
    except ImportError:
        # AppleScript fallback
        if modifiers:
            mod_str = " ".join(f"{m} down" for m in modifiers)
            _run_applescript(f"""
                tell application "System Events"
                    {mod_str}
                    keystroke "{key}"
                    {" ".join(f"{m} up" for m in modifiers)}
                end tell
            """)
        else:
            _run_applescript(f'tell application "System Events" to keystroke "{key}"')


def hotkey(keys: list):
    """Press a hotkey combination. E.g., hotkey(['cmd', 'space'])"""
    if len(keys) < 1:
        return
    main_key = keys[-1]
    modifiers = keys[:-1]
    press_key(main_key, modifiers)


def type_shortcut(modifier: str, key: str):
    """Convenience: type a shortcut like cmd+c, cmd+v."""
    press_key(key, [modifier])


def copy():
    hotkey(["cmd", "c"])


def paste():
    hotkey(["cmd", "v"])


def cut():
    hotkey(["cmd", "x"])


def select_all():
    hotkey(["cmd", "a"])


def undo():
    hotkey(["cmd", "z"])


def enter():
    press_key("return")


def tab():
    press_key("tab")


def escape():
    press_key("escape")
