"""微信 platform adapter — via itchaty/wechaty or webhook proxy.

微信 integration is more complex due to:
- No official bot API for personal accounts
- itchat/uos based approaches are frequently blocked
- Best approach: Use 企业微信 (WeCom) bot API or webhook proxy

This module provides the WeCom bot adapter as the primary path.
"""

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class WeComBot:
    """企业微信 (WeChat Work) bot adapter.

    Setup:
    1. Create a bot in 企业微信 group
    2. Copy the webhook URL
    3. Set WECOM_WEBHOOK_URL env var
    """

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("WECOM_WEBHOOK_URL", "")
        if not self.webhook_url:
            logger.warning("WECOM_WEBHOOK_URL not set.")

    def send_text(self, text: str, mentioned_list: list | None = None) -> bool:
        """Send text to 企业微信 group."""
        if not self.webhook_url:
            return False

        payload = {
            "msgtype": "text",
            "text": {
                "content": text,
                "mentioned_list": mentioned_list or [],
            },
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                logger.debug("WeCom message sent")
                return True
            logger.warning("WeCom send failed: %s", data)
            return False
        except Exception as e:
            logger.error("WeCom webhook error: %s", e)
            return False

    def send_markdown(self, content: str) -> bool:
        """Send markdown message."""
        if not self.webhook_url:
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.json().get("errcode") == 0
        except Exception as e:
            logger.error("WeCom markdown error: %s", e)
            return False

    def send_news(self, articles: list) -> bool:
        """Send news/articles card."""
        if not self.webhook_url:
            return False

        payload = {
            "msgtype": "news",
            "news": {"articles": articles},
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.json().get("errcode") == 0
        except Exception as e:
            logger.error("WeCom news error: %s", e)
            return False


class WechatProxyAdapter:
    """Adapter for wechat via a proxy/middleman service.

    Since direct wechat personal account APIs are unstable,
    this adapter expects a proxy service that:
    - Receives wechat messages → forwards to 天工
    - Receives 天工 responses → sends back to wechat

    The proxy URL is set via WECHAT_PROXY_URL env var.
    """

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url or os.environ.get("WECHAT_PROXY_URL", "")

    def send_message(self, user_id: str, text: str) -> bool:
        """Send a message to a wechat user via proxy."""
        if not self.proxy_url:
            return False

        try:
            resp = requests.post(
                f"{self.proxy_url}/send",
                json={"user_id": user_id, "text": text},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error("Wechat proxy error: %s", e)
            return False

    def poll_messages(self) -> list:
        """Poll for new messages from proxy."""
        if not self.proxy_url:
            return []

        try:
            resp = requests.get(f"{self.proxy_url}/messages", timeout=10)
            if resp.status_code == 200:
                return resp.json().get("messages", [])
        except Exception as e:
            logger.error("Wechat poll error: %s", e)
        return []
