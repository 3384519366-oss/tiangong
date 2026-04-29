---
name: 网络搜索
description: 使用搜索引擎查找最新信息、文档、新闻
requires_tools: [bash]
platforms: [macos]
---

# 网络搜索

当需要查找最新信息时，使用以下方式：

## 搜索策略
- 技术问题 → 优先查官方文档 + GitHub Issues
- 新闻事件 → 使用 `open "https://www.google.com/search?q=..."` 打开浏览器
- 中文内容 → 使用百度/知乎搜索

## 命令行搜索
```bash
# Google 搜索
open "https://www.google.com/search?q=Python+asyncio+tutorial"

# GitHub 搜索
open "https://github.com/search?q=fastapi+middleware&type=repositories"

# 百度搜索
open "https://www.baidu.com/s?wd=Python协程"
```

## URL 提取规范
- 永远不要编造 URL — 如果不知道确切的 URL，诚实说明
- 使用已知的官方域名
- 优先使用 HTTPS
