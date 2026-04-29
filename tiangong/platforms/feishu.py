"""飞书（Lark）Bot adapter — WebSocket 长连接优先，Webhook 降级。[P0 修复]

基于 lark-oapi WebSocket 长连接模式，实现：
- 收发闭环（实时接收消息 + 回复）
- 精确 @ 匹配（mention.id.open_id）
- Bot 自循环防护（sender_id == bot_open_id）
- 上下文记忆（按 chat_id 隔离 session）
- 无公网 URL 需求

依赖: pip install lark-oapi
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── lark-oapi 软依赖 ───────────────────────────────────

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageResponse,
    )
    from lark_oapi.api.bot.v3 import GetBotInfoRequest, GetBotInfoResponse
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    logger.warning("lark-oapi 未安装，飞书 Bot 将使用 Webhook 降级模式。\n"
                   "安装: pip install lark-oapi")


# ── Webhook 降级（保留旧功能）─────────────────────────────────

class FeishuWebhookBot:
    """飞书 custom bot webhook adapter (降级模式)."""

    def __init__(self, webhook_url: str | None = None):
        import requests  # 延迟导入
        self.webhook_url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL", "")
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK_URL not set. 飞书 webhook disabled.")

    def send_text(self, text: str) -> bool:
        if not self.webhook_url:
            return False
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            import requests
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            return data.get("code") == 0 or data.get("StatusMsg") == "success"
        except Exception as e:
            logger.error("Feishu webhook error: %s", e)
            return False


# ── WebSocket 主模式 ───────────────────────────────────

class FeishuSessionManager:
    """按 chat_id 隔离的会话管理器，支持上下文记忆。"""

    def __init__(self, agent_factory, max_history: int = 20):
        self.agent_factory = agent_factory
        self.max_history = max_history
        self._sessions: Dict[str, Any] = {}  # chat_id -> agent
        self._lock = threading.Lock()

    def get_agent(self, chat_id: str):
        with self._lock:
            if chat_id not in self._sessions:
                self._sessions[chat_id] = self.agent_factory()
            return self._sessions[chat_id]

    def clear_session(self, chat_id: str):
        with self._lock:
            if chat_id in self._sessions:
                del self._sessions[chat_id]


class FeishuWSBot:
    """飞书 WebSocket Bot — 完整实现。"""

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        agent_factory=None,
    ):
        if not LARK_AVAILABLE:
            raise RuntimeError("lark-oapi 未安装，请运行: pip install lark-oapi")

        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        if not self.app_id or not self.app_secret:
            raise RuntimeError("FEISHU_APP_ID 和 FEISHU_APP_SECRET 必须设置")

        self.agent_factory = agent_factory
        self.session_mgr: Optional[FeishuSessionManager] = None
        self.bot_open_id: str = ""
        self.bot_name: str = ""
        self._client: Optional[Any] = None

    def _build_client(self):
        """构建 lark-oapi 客户端。"""
        return lark.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()

    def _fetch_bot_info(self):
        """获取 Bot 自身信息（用于防自循环）。"""
        client = self._build_client()
        req = GetBotInfoRequest.builder().build()
        resp: GetBotInfoResponse = client.bot.v3.get_bot_info(req)
        if resp.success():
            bot = resp.data.bot
            self.bot_open_id = bot.open_id
            self.bot_name = bot.name
            logger.info("飞书 Bot 信息: name=%s open_id=%s", self.bot_name, self.bot_open_id)
        else:
            logger.warning("获取 Bot 信息失败: %s", resp.msg)

    def _send_text(self, chat_id: str, text: str) -> bool:
        """发送文本消息到指定聊天。"""
        client = self._build_client()
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .receive_id(chat_id) \
            .request_body(
                CreateMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            ).build()
        resp: CreateMessageResponse = client.im.v1.create_message(req)
        if resp.success():
            logger.debug("发送消息成功: chat_id=%s", chat_id)
            return True
        else:
            logger.warning("发送消息失败: %s", resp.msg)
            return False

    def _should_reply(self, event) -> tuple[bool, str]:
        """判断是否需要回复，返回 (should_reply, clean_text)。

        触发策略:
        - @自己 → 响应
        - 无@ + 关键词 → 响应
        - @别人 → 不响应
        - 自己发的消息 → 不响应
        """
        message = event.message
        sender = event.sender

        # 1. 防自循环
        sender_open_id = sender.sender_id.open_id
        if sender_open_id == self.bot_open_id:
            return False, ""

        # 2. 提取纯文本
        content = json.loads(message.content)
        text = content.get("text", "")

        # 3. 检查 @
        mentions = message.mentions or []
        mentioned_me = False
        mentioned_others = False

        for mention in mentions:
            mention_open_id = mention.id.open_id
            if mention_open_id == self.bot_open_id:
                mentioned_me = True
            else:
                mentioned_others = True

        # 4. 判断逻辑
        if mentioned_others and not mentioned_me:
            return False, ""

        if mentioned_me:
            # 移除 @ 前缀
            clean_text = text.replace(f"@{self.bot_name}", "").strip()
            return True, clean_text

        # 无@ + 关键词触发 (可选)
        return True, text.strip()

    def _on_message(self, data):
        """WebSocket 消息处理回调。"""
        if not self.agent_factory:
            return

        event = data.event
        message = event.message
        chat_id = message.chat_id
        chat_type = message.chat_type  # "p2p" or "group"

        should_reply, clean_text = self._should_reply(event)
        if not should_reply or not clean_text:
            return

        logger.info("飞书消息 [%s]: %s", chat_id, clean_text[:100])

        # 获取或创建会话 Agent
        agent = self.session_mgr.get_agent(chat_id)

        try:
            response = agent.chat(clean_text)
            if response:
                self._send_text(chat_id, response)
        except Exception as e:
            logger.exception("飞书消息处理失败: %s", e)
            self._send_text(chat_id, f"响应出错了: {str(e)[:200]}")

    def start(self):
        """启动 WebSocket 连接。"""
        if not LARK_AVAILABLE:
            logger.error("lark-oapi 未安装，无法启动飞书 WebSocket Bot")
            return

        # 获取 Bot 信息
        self._fetch_bot_info()

        # 初始化会话管理
        if self.agent_factory:
            self.session_mgr = FeishuSessionManager(self.agent_factory)

        # 构建 WebSocket client
        ws_client = lark.ws.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .event_handler(self._on_message) \
            .build()

        logger.info("飞书 WebSocket Bot 启动中...")
        ws_client.start()


# ── 统一 Gateway 接口 ──────────────────────────────────

class FeishuGateway:
    """飞书 Gateway — 自动选择 WebSocket 或 Webhook。"""

    def __init__(self, agent_factory=None):
        self.agent_factory = agent_factory
        self._ws_bot: Optional[FeishuWSBot] = None
        self._webhook_bot: Optional[FeishuWebhookBot] = None

    def start(self):
        """启动飞书 Bot。"""
        # 优先尝试 WebSocket
        if LARK_AVAILABLE and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
            try:
                self._ws_bot = FeishuWSBot(agent_factory=self.agent_factory)
                self._ws_bot.start()
                return
            except Exception as e:
                logger.warning("飞书 WebSocket 启动失败，回退到 Webhook: %s", e)

        # 降级到 Webhook
        self._webhook_bot = FeishuWebhookBot()
        logger.info("飞书 Webhook Bot 已初始化")

    def send_text(self, text: str) -> bool:
        """发送消息（Webhook 模式下有效）。"""
        if self._webhook_bot:
            return self._webhook_bot.send_text(text)
        logger.warning("飞书 Bot 未初始化，无法发送消息")
        return False


# ── CLI 入口 畜生 ──────────────────────────────────

def main():
    """Entry point: tiangong-feishu"""
    import sys
    logging.basicConfig(level=logging.INFO)

    if not LARK_AVAILABLE:
        print("错误: lark-oapi 未安装")
        print("请运行: pip install lark-oapi")
        sys.exit(1)

    if not os.environ.get("FEISHU_APP_ID") or not os.environ.get("FEISHU_APP_SECRET"):
        print("错误: 缺少环境变量")
        print("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        sys.exit(1)

    gateway = FeishuGateway()
    gateway.start()


if __name__ == "__main__":
    main()
