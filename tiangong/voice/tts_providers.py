"""TTS providers — Edge TTS (free, default) + 火山引擎 (premium Chinese)."""

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EdgeTTSProvider:
    """Microsoft Edge TTS — free, good quality, Chinese voice support."""

    # Chinese voices
    VOICES = {
        "zh-CN-female": "zh-CN-XiaoxiaoNeural",
        "zh-CN-male": "zh-CN-YunxiNeural",
        "zh-CN-xiaoxiao": "zh-CN-XiaoxiaoNeural",
        "zh-CN-yunxi": "zh-CN-YunxiNeural",
        "zh-CN-xiaoyi": "zh-CN-XiaoyiNeural",
        "zh-CN-yunjian": "zh-CN-YunjianNeural",
        "zh-TW-female": "zh-TW-HsiaoChenNeural",
        "zh-TW-male": "zh-TW-YunJheNeural",
    }

    def __init__(self, voice: str = "zh-CN-female"):
        self.voice = self.VOICES.get(voice, voice)

    async def _generate(self, text: str, output_path: str):
        import edge_tts
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_path)

    def speak(self, text: str, output_path: Optional[str] = None) -> Path:
        """Generate speech audio and save to file. Returns the path."""
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".mp3", prefix="tiangong_tts_")
            output_path = Path(output_path)

        output_path = Path(output_path)
        try:
            asyncio.run(self._generate(text, str(output_path)))
            logger.debug("TTS generated: %s (%d chars)", output_path, len(text))
            return output_path
        except Exception as e:
            logger.error("Edge TTS failed: %s", e)
            raise

    def speak_and_play(self, text: str) -> bool:
        """Generate speech and play it immediately using afplay."""
        try:
            path = self.speak(text)
            subprocess.run(["afplay", str(path)], check=True, timeout=30)
            path.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error("TTS playback failed: %s", e)
            return False


class HuoshanTTSProvider:
    """火山引擎 TTS — premium Chinese voice quality.

    Requires: HUOSHAN_APP_ID and HUOSHAN_TOKEN env vars.
    """

    def __init__(self, voice: str = "zh_female_qingxin"):
        import os
        self.app_id = os.environ.get("HUOSHAN_APP_ID", "")
        self.token = os.environ.get("HUOSHAN_TOKEN", "")
        self.voice = voice

    def speak(self, text: str, output_path: Optional[str] = None) -> Path:
        """Generate speech via 火山引擎 API."""
        if not self.app_id or not self.token:
            raise RuntimeError("火山引擎 TTS requires HUOSHAN_APP_ID and HUOSHAN_TOKEN")

        import requests
        import json

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".mp3", prefix="tiangong_tts_")
            output_path = Path(output_path)

        # 火山引擎 TTS API endpoint
        url = "https://openspeech.bytedance.com/api/v1/tts"
        headers = {
            "Authorization": f"Bearer; {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "app": {"appid": self.app_id, "token": self.token},
            "user": {"uid": "tiangong"},
            "audio": {"voice_type": self.voice, "encoding": "mp3", "speed_ratio": 1.0},
            "request": {"text": text, "text_type": "plain"},
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 3000:
            raise RuntimeError(f"火山引擎 TTS error: {data.get('message', 'unknown')}")

        output_path.write_bytes(resp.content)
        return output_path


def get_tts_provider(name: str = "edge"):
    """Factory for TTS providers."""
    if name == "edge":
        return EdgeTTSProvider()
    elif name == "huoshan":
        return HuoshanTTSProvider()
    else:
        raise ValueError(f"Unknown TTS provider: {name}")
