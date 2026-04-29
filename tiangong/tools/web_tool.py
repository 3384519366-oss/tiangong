"""Web 工具 — 网络搜索 + 网页抓取。[CC]

借鉴 Claude Code: WebSearch (搜索) + WebFetch (抓取网页内容)。
使用 Bing 国内版搜索，无需 API key，中文结果更准确。
"""

import ipaddress
import json
import logging
import re
import socket
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from tiangong.core.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)


def _is_private_host(hostname: str | None) -> bool:
    """SSRF 防护：检查 hostname 是否指向内网/本地地址。"""
    if not hostname:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        # IPv6-mapped IPv4 解包
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    except ValueError:
        pass
    # 域名 — 解析后检查
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for r in resolved:
            addr = r[4][0]
            ip = ipaddress.ip_address(addr)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                return True
    except socket.gaierror:
        return False
    return False

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "在互联网上搜索信息，返回相关结果的标题、摘要和链接。"
        "基于 Bing 国内版，无需 API key。用于查询最新文档、解决方案和实时信息。\n"
        "【重要】每次搜索最多发 1-3 个关键词组合。获取结果后不要重复搜索相同内容。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词。",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数（默认 5，上限 10）。",
            },
        },
        "required": ["query"],
    },
}

WEB_FETCH_SCHEMA = {
    "name": "web_fetch",
    "description": (
        "获取指定 URL 的网页内容，提取纯文本。用于阅读文档、文章和参考资料。"
        "自动提取正文内容，去除导航、广告等无关元素。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要获取的网页 URL。",
            },
            "max_chars": {
                "type": "integer",
                "description": "最大返回字符数（默认 10000）。",
            },
        },
        "required": ["url"],
    },
}


class _TextExtractor(HTMLParser):
    """HTML 正文提取器——去除 script/style，保留文本。"""

    def __init__(self):
        super().__init__()
        self.text_parts: List[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "nav", "header", "footer", "aside", "noscript"}
        self._block_tags = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "hr", "section", "article"}
        self._tag_stack: List[str] = []

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        self._tag_stack.append(tag_l)
        if tag_l in self._skip_tags:
            self._skip = True
        elif tag_l in self._block_tags:
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag_l:
            self._tag_stack.pop()
        if tag_l in self._skip_tags:
            self._skip = False
        elif tag_l in self._block_tags:
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.text_parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        # 压缩多余空白
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        raw = re.sub(r' {2,}', ' ', raw)
        return raw.strip()


def web_search_tool(args: dict, **kwargs) -> str:
    query = args.get("query", "").strip()
    max_results = min(args.get("max_results", 5), 10)

    if not query:
        return tool_error("搜索查询不能为空。")
    if len(query) < 2:
        return tool_error("搜索查询至少需要2个字符。")

    results = []

    # 使用 Bing 国内版搜索
    try:
        import urllib.parse
        encoded = urllib.parse.quote(query)
        # Bing 国内版，语言设为中文
        url = f"https://cn.bing.com/search?q={encoded}&setmkt=zh-CN&setlang=zh"

        req = urllib.request.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = _parse_bing_html(html, max_results)
    except Exception as e:
        logger.warning("Bing 搜索失败: %s，尝试备用方案", e)

    if not results:
        # 备用：返回搜索链接
        return tool_result({
            "query": query,
            "results": [],
            "hint": (
                f"搜索未返回结果。请手动访问: "
                f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
            ),
        })

    return tool_result({
        "query": query,
        "results": results,
        "count": len(results),
    })


def _parse_bing_html(html: str, max_results: int) -> List[dict]:
    """从 Bing 搜索结果页提取结果。"""
    results = []

    # Bing 搜索结果的标准格式:
    # 每个结果在 <li class="b_algo"> 中
    # 标题: <h2><a href="URL">TITLE</a></h2>
    # 摘要: <p> 或 <div class="b_caption">

    algo_pattern = re.compile(
        r'<li class="b_algo"[^>]*>(.*?)</li>',
        re.DOTALL | re.IGNORECASE,
    )
    algo_blocks = algo_pattern.findall(html)

    for block in algo_blocks[:max_results]:
        # 提取 URL 和标题
        link_match = re.search(
            r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if not link_match:
            # 更宽松的链接匹配
            link_match = re.search(
                r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
        if not link_match:
            continue

        url = link_match.group(1)
        title_raw = link_match.group(2)

        # 提取摘要
        snippet = ""
        # 尝试从 b_caption 提取
        caption_match = re.search(
            r'<div class="b_caption"[^>]*>(.*?)</div>\s*(?:</li>|$)',
            block, re.DOTALL | re.IGNORECASE,
        )
        if caption_match:
            snippet = caption_match.group(1)
        else:
            # 尝试从 <p> 提取
            p_match = re.search(
                r'<p[^>]*>(.*?)</p>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if p_match:
                snippet = p_match.group(1)

        # 清理 HTML 标签
        title = re.sub(r'<[^>]+>', '', title_raw).strip()
        snippet = re.sub(r'<[^>]+>', '', snippet).strip()

        # 过滤无效结果
        if not title or not url:
            continue
        if url.startswith(("http://", "https://")):
            results.append({
                "title": title[:200],
                "url": url,
                "snippet": snippet[:500],
            })

    return results


def web_fetch_tool(args: dict, **kwargs) -> str:
    url = args.get("url", "").strip()
    max_chars = min(args.get("max_chars", 10000), 50000)

    if not url:
        return tool_error("URL 不能为空。")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # 基本 URL 验证 + SSRF 防护
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return tool_error(f"无效的 URL: {url}")
        if _is_private_host(parsed.hostname):
            return tool_error(f"禁止访问内网地址: {parsed.hostname}")
    except Exception:
        return tool_error(f"无效的 URL: {url}")

    try:
        # SSRF 防护：自定义重定向处理器，检查每个跳转目标
        class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed_new = urlparse(newurl)
                if _is_private_host(parsed_new.hostname):
                    raise urllib.error.URLError(f"SSRF: 禁止重定向到内网地址 {parsed_new.hostname}")
                return urllib.request.HTTPRedirectHandler.redirect_request(
                    self, req, fp, code, msg, headers, newurl)

        req = urllib.request.Request(url, headers={
            "User-Agent": "TianGong/1.0 (AI Assistant; +https://github.com/tiangong)",
            "Accept": "text/html,application/xhtml+xml,text/plain",
        })
        opener = urllib.request.build_opener(_SSRFRedirectHandler())
        with opener.open(req, timeout=20) as resp:
            # 检查 Content-Type
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return tool_error(
                    f"不支持的内容类型: {content_type}。"
                    f"WebFetch 只支持 HTML 和纯文本。"
                )

            raw = resp.read()
            # 尝试检测编码
            charset = "utf-8"
            ct_match = re.search(r'charset=([\w-]+)', content_type)
            if ct_match:
                charset = ct_match.group(1)

            try:
                html = raw.decode(charset, errors="replace")
            except (UnicodeDecodeError, LookupError):
                html = raw.decode("utf-8", errors="replace")

            # 纯文本直接返回
            if "text/plain" in content_type:
                text = html[:max_chars]
                if len(html) > max_chars:
                    text += f"\n... (已截断，全文 {len(html)} 字符)"
                return tool_result({
                    "url": url,
                    "content_type": content_type,
                    "content": text,
                })

            # HTML → 纯文本
            extractor = _TextExtractor()
            extractor.feed(html)
            text = extractor.get_text()

            if len(text) > max_chars:
                text = text[:max_chars] + (
                    f"\n... (已截断，全文 {len(text)} 字符)"
                )

            return tool_result({
                "url": url,
                "content_type": content_type,
                "content": text,
            })

    except urllib.error.HTTPError as e:
        return tool_error(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return tool_error(f"连接失败: {e.reason}")
    except Exception as e:
        return tool_error(f"获取失败: {e}")


registry.register(
    name="web_search",
    toolset="核心",
    schema=WEB_SEARCH_SCHEMA,
    handler=web_search_tool,
    description="在互联网上搜索信息",
    emoji="🌐",
    display_name="网络搜索",
)

registry.register(
    name="web_fetch",
    toolset="核心",
    schema=WEB_FETCH_SCHEMA,
    handler=web_fetch_tool,
    description="获取指定 URL 的网页内容",
    emoji="📄",
    display_name="网页抓取",
)
