"""STT providers — local faster-whisper (default) + 火山引擎 ASR."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LocalWhisperProvider:
    """Local faster-whisper STT. Requires faster-whisper package."""

    def __init__(self, model_size: str = "base", language: str = "zh"):
        self.model_size = model_size
        self.language = language
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded: %s", self.model_size)
        except Exception as e:
            raise RuntimeError(f"Failed to load whisper model: {e}")

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file to text."""
        self._load_model()
        segments, _ = self._model.transcribe(audio_path, language=self.language, beam_size=5)
        text = " ".join(seg.text for seg in segments)
        logger.debug("STT: %s", text[:100])
        return text.strip()

    def record_and_transcribe(self, duration: int = 10) -> str:
        """Record audio from mic and transcribe.

        Uses macOS `rec` command (sox) or ffmpeg.
        """
        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="tiangong_stt_")
        os.close(fd)

        try:
            # Try sox first
            subprocess.run(
                ["rec", "-r", "16000", "-c", "1", "-b", "16", temp_path, "trim", "0", str(duration)],
                capture_output=True, timeout=duration + 5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Fallback: ffmpeg
            try:
                subprocess.run(
                    ["ffmpeg", "-f", "avfoundation", "-i", ":0", "-t", str(duration),
                     "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", temp_path, "-y"],
                    capture_output=True, timeout=duration + 5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                raise RuntimeError("No recording tool available. Install sox or ffmpeg.")

        try:
            text = self.transcribe(temp_path)
            return text
        finally:
            Path(temp_path).unlink(missing_ok=True)


class HuoshanASRProvider:
    """火山引擎 ASR — premium Chinese speech recognition.

    Requires: HUOSHAN_APP_ID and HUOSHAN_TOKEN env vars.
    """

    def __init__(self):
        self.app_id = os.environ.get("HUOSHAN_APP_ID", "")
        self.token = os.environ.get("HUOSHAN_TOKEN", "")

    def transcribe(self, audio_path: str) -> str:
        if not self.app_id or not self.token:
            raise RuntimeError("火山引擎 ASR requires HUOSHAN_APP_ID and HUOSHAN_TOKEN")

        import requests
        import json
        import base64

        audio_data = base64.b64encode(Path(audio_path).read_bytes()).decode()

        url = "https://openspeech.bytedance.com/api/v1/asr"
        payload = {
            "app": {"appid": self.app_id, "token": self.token},
            "user": {"uid": "tiangong"},
            "audio": {"format": "wav", "rate": 16000, "bits": 16, "channel": 1, "language": "zh-CN"},
            "request": {"model": "bigmodel", "show_utterances": False},
            "audio_data": audio_data,
        }

        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 1000:
            raise RuntimeError(f"火山引擎 ASR error: {data.get('message', 'unknown')}")

        return data.get("result", [{}])[0].get("text", "")


class MacOSDictationProvider:
    """Use macOS built-in dictation via keyboard shortcut.

    Simple but requires enabling dictation in System Preferences.
    """

    def record_and_transcribe(self) -> str:
        """Trigger dictation via Fn key double-tap (macOS built-in)."""
        # macOS dictation is not easily scriptable, so this is a placeholder
        # User needs to enable: System Preferences > Keyboard > Dictation
        raise NotImplementedError("Use LocalWhisperProvider instead")


def get_stt_provider(name: str = "local"):
    """Factory for STT providers."""
    if name == "local" or name == "whisper":
        return LocalWhisperProvider()
    elif name == "huoshan":
        return HuoshanASRProvider()
    elif name == "macos":
        return MacOSDictationProvider()
    else:
        raise ValueError(f"Unknown STT provider: {name}")
