"""语音工具 — TTS（文字转语音）和 STT（语音识别）。"""

import json
import logging

from tiangong.core.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def voice_tool_handler(args: dict, **kwargs) -> str:
    """分发语音动作。"""
    action = args.get("action", "")

    if action == "speak":
        text = args.get("text", "")
        if not text:
            return tool_error("说话需要提供 text 参数。")

        provider_name = args.get("provider", "edge")

        try:
            from tiangong.voice.tts_providers import get_tts_provider
            provider = get_tts_provider(provider_name)
            ok = provider.speak_and_play(text)
            return tool_result({"spoken": True, "text": text[:200], "provider": provider_name})
        except Exception as e:
            return tool_error(f"语音合成失败: {e}")

    elif action == "listen":
        duration = int(args.get("duration", 5))
        provider_name = args.get("provider", "local")

        try:
            from tiangong.voice.stt_providers import get_stt_provider
            provider = get_stt_provider(provider_name)
            text = provider.record_and_transcribe(duration=duration)
            return tool_result({"text": text, "provider": provider_name})
        except NotImplementedError:
            return tool_error("录音不可用。请安装 sox: brew install sox")
        except Exception as e:
            return tool_error(f"语音识别失败: {e}")

    elif action == "transcribe":
        file_path = args.get("file", "")
        if not file_path:
            return tool_error("转写需要提供 file 路径。")
        provider_name = args.get("provider", "local")

        try:
            from tiangong.voice.stt_providers import get_stt_provider
            provider = get_stt_provider(provider_name)
            text = provider.transcribe(file_path)
            return tool_result({"text": text, "file": file_path})
        except Exception as e:
            return tool_error(f"转写失败: {e}")

    elif action == "tts_file":
        text = args.get("text", "")
        if not text:
            return tool_error("需要提供 text。")
        output = args.get("output")

        try:
            from tiangong.voice.tts_providers import get_tts_provider
            provider = get_tts_provider(args.get("provider", "edge"))
            path = provider.speak(text, output_path=output)
            return tool_result({"file": str(path), "text": text[:200]})
        except Exception as e:
            return tool_error(f"语音文件生成失败: {e}")

    else:
        return tool_error(f"未知操作: {action}。可用: speak(说话), listen(收听), transcribe(转写), tts_file(存为音频)。")


VOICE_SCHEMA = {
    "name": "voice",
    "description": (
        "语音合成（TTS）和语音识别（STT）。"
        "操作: speak（文字转语音播放）、listen（麦克风录制并识别）、"
        "transcribe（转写音频文件）、tts_file（保存语音到文件）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["speak", "listen", "transcribe", "tts_file"],
                "description": "要执行的语音操作。"
            },
            "text": {"type": "string", "description": "要朗读的文本（speak/tts_file 用）。"},
            "duration": {"type": "integer", "description": "录音时长，单位秒（listen 用）。"},
            "file": {"type": "string", "description": "音频文件路径（transcribe 用）。"},
            "output": {"type": "string", "description": "输出文件路径（tts_file 用）。"},
            "provider": {"type": "string", "description": "服务商: edge/huoshan（TTS），local/huoshan（STT）。"},
        },
        "required": ["action"],
    },
}

registry.register(
    name="voice",
    toolset="语音",
    schema=VOICE_SCHEMA,
    handler=voice_tool_handler,
    description="语音合成（TTS）和语音识别（STT）",
    emoji="🎙️",
    display_name="语音",
)
