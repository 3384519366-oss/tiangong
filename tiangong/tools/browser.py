"""浏览器自动化工具 — Playwright 封装 [P1 补全]

提供 Agent 可调用的浏览器操作工具：
- 页面导航、点击、输入、滚动
- 截图、执行 JS
- 与 Agent 的视觉能力结合 (截图分析)

依赖: pip install playwright && playwright install chromium
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── 软依赖 ────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Page = Browser = BrowserContext = object
    logger.warning("playwright 未安装，浏览器工具不可用。\n"
                   "安装: pip install playwright && playwright install chromium")


# ── 全局浏览器实例管理 ───────────────────────────────────

class BrowserManager:
    """管理 Playwright 浏览器实例生命周期。"""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _ensure_initialized(self) -> Any:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("playwright 未安装")
        if self._page is None or self._page.is_closed():
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()
        return self._page

    def get_page(self) -> Page:
        return self._ensure_initialized()

    def close(self):
        if self._page:
            self._page.close()
            self._page = None
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None


# 全局管理器实例
_browser_manager = BrowserManager()


def _get_page() -> Any:
    return _browser_manager.get_page()


# ── 工具函数 (用于 MCP 注册) ───────────────────────────────────────

def navigate(url: str, wait_until: str = "networkidle") -> str:
    """导航到指定 URL。

    Args:
        url: 目标网址
        wait_until: 等待条件 (load/domcontentloaded/networkidle)
    """
    # SSRF 防护
    from urllib.parse import urlparse
    from .web_tool import _is_private_host
    parsed = urlparse(url)
    if _is_private_host(parsed.hostname):
        return json.dumps({"error": f"禁止访问内网地址: {parsed.hostname}", "message": "SSRF 防护拦截"}, ensure_ascii=False)

    try:
        page = _get_page()
        page.goto(url, wait_until=wait_until, timeout=30000)
        title = page.title()
        return json.dumps({
            "success": True,
            "title": title,
            "url": page.url,
            "message": f"已导航到: {title}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"导航失败: {str(e)}"
        }, ensure_ascii=False)


def click(selector: str, timeout: int = 5000) -> str:
    """点击页面元素。

    Args:
        selector: CSS 选择器
        timeout: 等待超时 (毫秒)
    """
    try:
        page = _get_page()
        page.click(selector, timeout=timeout)
        return json.dumps({
            "success": True,
            "message": f"已点击: {selector}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"点击失败: {str(e)}"
        }, ensure_ascii=False)


def type_text(selector: str, text: str, clear: bool = True) -> str:
    """在表单字段输入文本。

    Args:
        selector: CSS 选择器
        text: 要输入的文本
        clear: 是否先清除现有内容
    """
    try:
        page = _get_page()
        if clear:
            page.fill(selector, "")
        page.type(selector, text, delay=10)
        return json.dumps({
            "success": True,
            "message": f"已在 {selector} 输入: {text[:50]}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"输入失败: {str(e)}"
        }, ensure_ascii=False)


def submit_form(selector: str = "button[type=submit]") -> str:
    """提交表单。

    Args:
        selector: 提交按钮 CSS 选择器
    """
    try:
        page = _get_page()
        page.click(selector)
        page.wait_for_load_state("networkidle")
        return json.dumps({
            "success": True,
            "url": page.url,
            "message": "表单已提交"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"提交失败: {str(e)}"
        }, ensure_ascii=False)


def scroll(direction: str = "down", amount: int = 500) -> str:
    """滚动页面。

    Args:
        direction: down/up
        amount: 滚动像素数
    """
    try:
        page = _get_page()
        if direction == "down":
            page.mouse.wheel(0, amount)
        else:
            page.mouse.wheel(0, -amount)
        return json.dumps({
            "success": True,
            "message": f"已向{direction}滚动 {amount}px"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"滚动失败: {str(e)}"
        }, ensure_ascii=False)


def get_text(selector: Optional[str] = None) -> str:
    """获取页面文本内容。

    Args:
        selector: CSS 选择器 (留空则获取整个页面文本)
    """
    try:
        page = _get_page()
        if selector:
            element = page.query_selector(selector)
            if not element:
                return json.dumps({
                    "error": f"元素未找到: {selector}"
                }, ensure_ascii=False)
            text = element.inner_text()
        else:
            text = page.inner_text("body")
        return json.dumps({
            "success": True,
            "text": text[:8000],
            "message": f"获取文本成功 ({len(text)} 字符)"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"获取文本失败: {str(e)}"
        }, ensure_ascii=False)


def screenshot(path: Optional[str] = None, full_page: bool = False) -> str:
    """截图并保存。

    Args:
        path: 保存路径 (留空则保存到临时目录)
        full_page: 是否截取整个页面
    """
    try:
        page = _get_page()
        if not path:
            tmp_dir = os.path.join(os.path.expanduser("~"), ".tiangong", "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            import time
            path = os.path.join(tmp_dir, f"screenshot_{int(time.time())}.png")

        page.screenshot(path=path, full_page=full_page)
        return json.dumps({
            "success": True,
            "path": path,
            "message": f"截图已保存: {path}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"截图失败: {str(e)}"
        }, ensure_ascii=False)


def evaluate(script: str) -> str:
    """在页面上执行 JavaScript。

    Args:
        script: JavaScript 代码
    """
    try:
        page = _get_page()
        result = page.evaluate(script)
        return json.dumps({
            "success": True,
            "result": result,
            "message": "JS 执行成功"
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"JS 执行失败: {str(e)}"
        }, ensure_ascii=False)


def close_browser() -> str:
    """关闭浏览器释放资源。"""
    try:
        _browser_manager.close()
        return json.dumps({
            "success": True,
            "message": "浏览器已关闭"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "message": f"关闭失败: {str(e)}"
        }, ensure_ascii=False)


# ── 工具注册表 (用于动态加载) ──────────────────────────────────────

TOOL_REGISTRY = {
    "browser_navigate": {
        "function": navigate,
        "description": "导航到指定 URL",
        "parameters": {
            "url": {"type": "string", "description": "目标网址"},
            "wait_until": {"type": "string", "description": "等待条件 (load/domcontentloaded/networkidle)", "default": "networkidle"},
        },
        "required": ["url"],
    },
    "browser_click": {
        "function": click,
        "description": "点击页面元素",
        "parameters": {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "timeout": {"type": "integer", "description": "等待超时 (毫秒)", "default": 5000},
        },
        "required": ["selector"],
    },
    "browser_type": {
        "function": type_text,
        "description": "在表单字段输入文本",
        "parameters": {
            "selector": {"type": "string", "description": "CSS 选择器"},
            "text": {"type": "string", "description": "要输入的文本"},
            "clear": {"type": "boolean", "description": "先清除现有内容", "default": True},
        },
        "required": ["selector", "text"],
    },
    "browser_submit": {
        "function": submit_form,
        "description": "提交表单",
        "parameters": {
            "selector": {"type": "string", "description": "提交按钮 CSS 选择器", "default": "button[type=submit]"},
        },
        "required": [],
    },
    "browser_scroll": {
        "function": scroll,
        "description": "滚动页面",
        "parameters": {
            "direction": {"type": "string", "description": "滚动方向 (down/up)", "default": "down"},
            "amount": {"type": "integer", "description": "滚动像素数", "default": 500},
        },
        "required": [],
    },
    "browser_get_text": {
        "function": get_text,
        "description": "获取页面文本内容",
        "parameters": {
            "selector": {"type": "string", "description": "CSS 选择器 (留空则获取整页)", "default": None},
        },
        "required": [],
    },
    "browser_screenshot": {
        "function": screenshot,
        "description": "截图并保存",
        "parameters": {
            "path": {"type": "string", "description": "保存路径 (留空则自动生成)", "default": None},
            "full_page": {"type": "boolean", "description": "是否截取整个页面", "default": False},
        },
        "required": [],
    },
    "browser_evaluate": {
        "function": evaluate,
        "description": "在页面上执行 JavaScript",
        "parameters": {
            "script": {"type": "string", "description": "JavaScript 代码"},
        },
        "required": ["script"],
    },
    "browser_close": {
        "function": close_browser,
        "description": "关闭浏览器释放资源",
        "parameters": {},
        "required": [],
    },
}


def register_browser_tools(mcp_server):
    """将浏览器工具注册到 MCP Server。"""
    if not PLAYWRIGHT_AVAILABLE:
        logger.warning("浏览器工具未注册: playwright 不可用")
        return

    for name, spec in TOOL_REGISTRY.items():
        mcp_server.register_tool(
            name=name,
            func=spec["function"],
            description=spec["description"],
            parameters=spec["parameters"],
            required=spec.get("required", []),
        )
    logger.info("已注册 %d 个浏览器工具", len(TOOL_REGISTRY))
