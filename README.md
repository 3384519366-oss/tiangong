# 天工 AI 助手

为中国开发者而生的全中文智能编程搭档，由 **chengzi-AI 团队** 开源。

融合 Hermes、Claude Code、smolagents 三大框架精华，支持 9 家模型 Provider、20 个内置工具、完整 macOS 屏幕操控。

## 快速开始

```bash
# 安装
pip install -e .

# 可选依赖
pip install -e ".[voice]"      # 语音合成/识别
pip install -e ".[browser]"    # 浏览器自动化
pip install -e ".[all]"        # 全部功能

# 启动
tiangong
```

首次运行会自动进入配置向导，也可直接编辑 `~/.tiangong/config.yaml`：

```yaml
providers:
  openai:
    type: openai
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    models:
      gpt4o:
        name: gpt-4o
        display_name: GPT-4o
        max_tokens: 4096
```

## 功能概览

| 模块 | 说明 |
|------|------|
| **模型** | 9 家 Provider 统一接入，运行时 `/model` 热切换 |
| **工具** | 20 个工具：文件读写、代码搜索、网络检索、bash/sandbox 执行、子代理委派、macOS 屏幕操控 |
| **记忆** | 3 层存储（文件 + ChromaDB 向量 + SQLite），多信号语义融合 |
| **安全** | 命令审批、沙箱资源限制、熔断重试、Git 快照回滚 |
| **上下文** | Token 估算 + LLM 摘要压缩，128K 窗口自适应 |
| **Code Agent** | AST 安全校验 + 子进程隔离 Python 代码执行 |
| **语音** | Edge TTS 合成 + faster-whisper 识别 |
| **平台** | macOS 菜单栏 / 企业微信 / 飞书 WebSocket |

## CLI 交互

```
! 命令         直接执行 bash
@ 路径         引用文件内容
/help          命令面板（方向键 + 打字筛选）
/model         交互式切换模型
/memory        查看记忆库
/config        查看当前配置
```

## 项目结构

```
tiangong/
  core/         Agent 核心、LLM 门面、上下文压缩、工具注册/执行
  tools/         20 个内置工具
  memory/        记忆系统（ChromaDB + SQLite + 文件）
  guard/         安全体系（审批/沙箱/快照/熔断）
  computer/      macOS 屏幕操控（截图/鼠标/键盘/视觉循环）
  skills/        技能系统（3 级渐进加载）
  voice/         语音合成与识别
  platforms/     桌面应用 / 微信 / 飞书适配
```

## 要求

- Python >= 3.11
- macOS（屏幕操控功能依赖）
- ChromaDB、Rich、OpenAI SDK

## 开源协议

MIT License. Copyright (c) 2025 chengzi-AI.
