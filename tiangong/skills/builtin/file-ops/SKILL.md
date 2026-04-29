---
name: 文件操作
description: 文件的读取、写入、搜索、批量处理操作指南
requires_tools: [bash]
platforms: [macos]
---

# 文件操作

## 安全原则
- 写入前先备份或创建检查点
- 批量操作前先在单个文件上测试
- 操作重要文件前告知 Boss

## 文件判断
使用 `file` 命令判断文件类型：
```bash
file document.pdf
# PDF document, version 1.4

file data.bin
# data
```

## 编码检测
```bash
file -I document.txt
# document.txt: text/plain; charset=utf-8
```

## 高效搜索
```bash
# 按名称搜索
find . -name "*.py" -not -path "./node_modules/*"

# 按内容搜索（macOS 带颜色）
grep --color=auto -r "TODO" --include="*.py" .

# 按大小搜索
find . -type f -size +1M
```

## 批量处理范例
```bash
# 转换所有 PNG 为 JPG
for f in *.png; do sips -s format jpeg "$f" --out "${f%.png}.jpg"; done

# 在所有 Python 文件头部添加编码声明
for f in $(grep -rL "coding: utf-8" --include="*.py" .); do
  sed -i '' '1i\
# -*- coding: utf-8 -*-
' "$f"
done
```
