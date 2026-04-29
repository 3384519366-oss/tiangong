"""天工 macOS Desktop App — menu bar + global shortcut.

Uses rumps for the menu bar component.
Global hotkey via pynput or Carbon events.
"""

import logging
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import rumps
    RUMPS_AVAILABLE = True
except ImportError:
    RUMPS_AVAILABLE = False


class 天工MenuBar:
    """macOS menu bar app for 天工."""

    def __init__(self):
        if not RUMPS_AVAILABLE:
            raise RuntimeError("rumps not installed. Run: pip install rumps")

        self.app = rumps.App("天工", title="🧠", quit_button=None)
        self._setup_menu()
        self._agent_process = None

    def _setup_menu(self):
        """Build the menu bar menu."""

        @self.app.rumps.clicked("新会话")
        def new_session(_):
            rumps.alert(title="天工", message="请在终端中运行 tiangong 开始新会话。")

        @self.app.rumps.clicked("语音输入")
        def voice_input(_):
            try:
                from tiangong.voice.stt_providers import LocalWhisperProvider
                provider = LocalWhisperProvider()
                # Quick 5-second recording
                rumps.alert(title="天工", message="即将开始 5 秒录音...")
                text = provider.record_and_transcribe(duration=5)
                if text:
                    # Send to agent
                    rumps.alert(title="识别结果", message=text)
                else:
                    rumps.alert(title="天工", message="未识别到语音。")
            except Exception as e:
                rumps.alert(title="错误", message=str(e))

        @self.app.rumps.clicked("截图分析")
        def screenshot_analysis(_):
            rumps.alert(title="天工", message="请在终端中使用 computer screenshot 命令。")

        @self.app.rumps.separator
        def sep1(_):
            pass

        @self.app.rumps.clicked("关于 天工")
        def about(_):
            rumps.alert(
                title="天工 — 中文 AI 助手",
                message="基于 DeepSeek V4 构建的全能 AI 助手\n"
                        "模型: DeepSeek V4 Flash\n"
                        "环境: macOS\n"
                        "项目: ~/.tiangong/",
            )

        @self.app.rumps.clicked("退出")
        def quit_app(_):
            rumps.quit_application()

    def run(self):
        """Start the menu bar app (blocking)."""
        logger.info("Starting 天工 menu bar app...")
        self.app.run()


def start_desktop():
    """Start the desktop app. Non-blocking — runs in a thread."""
    if not RUMPS_AVAILABLE:
        print("rumps not available. Install: pip install rumps")
        return

    app = 天工MenuBar()
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    return app


def main():
    """Entry point for tiangong-desktop command."""
    if not RUMPS_AVAILABLE:
        print("rumps not installed. Run: pip install rumps")
        sys.exit(1)

    # Setup logging
    logging.basicConfig(level=logging.INFO)

    app = 天工MenuBar()
    app.run()


if __name__ == "__main__":
    main()
