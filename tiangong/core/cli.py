"""天工 CLI 界面 — "天工开物"主题。[原创]

古匠精神，今技呈现 — 从青铜铭文到终端美学。
"""

import sys
import tty
import termios

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.markup import escape
from rich import box

console = Console()

# ═══════════════════════════════════════
# 天工开物 · 色彩体系
# ═══════════════════════════════════════

BRONZE      = "#c9a84c"   # 古铜金 — 品牌主色
BRONZE_DIM  = "#8b7355"   # 铜锈
INK         = "#1a1a24"   # 墨色
JADE        = "#4ade80"   # 玉色 — 用户输入
VERMILLION  = "#ef4444"   # 朱砂 — 错误
ICE_BLUE    = "#7dd3fc"   # 冰蓝 — 读取/搜索
AMBER       = "#fbbf24"   # 琥珀 — 执行
PURPLE      = "#c084fc"   # 紫 — 写入
PARCHMENT   = "#f0ead6"   # 宣纸白 — 正文
MIST        = "#9ca3af"   # 薄雾灰 — 次要文字
SILK        = "#d4c5a9"   # 丝绸 — 温暖中性

# 工具分类 → 边框颜色
_TOOL_COLOR_MAP = {
    # 信息获取 — 冰蓝
    "read": ICE_BLUE, "grep": ICE_BLUE, "codebase": ICE_BLUE,
    "web_search": ICE_BLUE, "web_fetch": ICE_BLUE, "notebook_read": ICE_BLUE,
    # 内容修改 — 紫
    "write": PURPLE, "edit": PURPLE, "notebook_edit": PURPLE,
    # 代码执行 — 琥珀
    "bash": AMBER, "sandbox": AMBER, "bg_task": AMBER,
    # 系统 — 薄雾
    "delegate": MIST, "task": MIST, "mcp": MIST,
    "skill": MIST, "memory": MIST, "computer": MIST, "voice": MIST,
}

_MOTTO = "为中国开发者而生的全中文智能编程搭档"


def _tool_color(tool_name: str) -> str:
    return _TOOL_COLOR_MAP.get(tool_name, MIST)


# ═══════════════════════════════════════
# 组件
# ═══════════════════════════════════════

_COMMANDS = {
    "会话": [
        ("/clear", "重置会话"),
        ("/save", "保存当前会话"),
        ("/load <id>", "恢复指定会话"),
        ("/sessions", "列出已保存的会话"),
        ("/compress", "查看上下文压缩统计"),
    ],
    "信息": [
        ("/help", "显示全部可用命令"),
        ("/tools", "查看工具及分类"),
        ("/config", "查看当前配置"),
        ("/memory", "查看记忆库"),
        ("/bg", "后台任务状态"),
        ("/delegate", "子代理状态"),
    ],
    "设置": [
        ("/model", "切换 AI 模型"),
        ("/voice", "切换语音播报 开/关"),
    ],
    "系统": [
        ("/quit", "退出天工"),
    ],
}


def _getch():
    """读取单个按键 — 支持方向键。macOS/Linux 终端。"""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                return '\x1b[' + ch3
            return '\x1b'
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def interactive_command_picker():
    """交互式命令选择器 — 打字筛选 + 方向键导航，Enter 执行，Esc 返回。

    Returns: 选中的命令字符串，或 None（取消/失败）
    """
    flat = []
    for group, cmds in _COMMANDS.items():
        for cmd, desc in cmds:
            flat.append((cmd, desc, group))

    if not flat:
        return None

    console.print()
    filt = ""
    idx = 0

    def _filtered():
        if not filt:
            return flat
        lo = filt.lower()
        return [(c, d, g) for c, d, g in flat if lo in c.lower() or lo in d]

    def _build(sel, fstr, items):
        lines = []
        last_group = None
        for i, (cmd, desc, group) in enumerate(items):
            if group != last_group:
                lines.append(f"\n ◆  {group}")
                last_group = group
            if i == sel:
                lines.append(f" ▶ [{BRONZE}]{cmd:<16}[/] [bold {PARCHMENT}]{desc}[/]")
            else:
                lines.append(f"   [{BRONZE}]{cmd:<16}[/] [dim]{desc}[/]")
        lines.append(f"\n [dim]↑↓ 选择   Enter 执行   Esc 返回[/]")
        lines.append(f"     [{JADE}]/{fstr}▌[/]")
        body = Text.from_markup("\n".join(lines))
        return Panel(body, border_style=BRONZE, box=box.ROUNDED,
                     title="⌨  命令列表", title_align="left", padding=(1, 2))

    try:
        items = _filtered()
        with Live(_build(idx, filt, items), console=console, auto_refresh=False, transient=True) as live:
            while True:
                key = _getch()
                if key == '\x1b[A':  # Up
                    if items:
                        idx = (idx - 1) % len(items)
                elif key == '\x1b[B':  # Down
                    if items:
                        idx = (idx + 1) % len(items)
                elif key in ('\r', '\n'):  # Enter
                    return items[idx][0] if items else None
                elif key == '\x1b':  # Esc
                    return None
                elif key in ('\x7f', '\b'):  # Backspace
                    filt = filt[:-1]
                    items = _filtered()
                    idx = 0
                elif len(key) == 1 and key.isprintable():
                    filt += key
                    items = _filtered()
                    idx = 0
                else:
                    continue
                live.update(_build(idx, filt, items), refresh=True)
    except Exception:
        print_help_panel()
        return None


def print_tools_panel(tools: list):
    """工具面板 — 按 toolset 分组，带 emoji 和描述。"""
    # 按 toolset 分组
    groups: dict = {}
    for t in tools:
        ts = t.get("toolset", "其他")
        groups.setdefault(ts, []).append(t)

    from rich.table import Table
    table = Table(box=box.SIMPLE, border_style=BRONZE_DIM, show_header=False,
                  padding=(0, 1), expand=True)

    def _color(toolset: str) -> str:
        return {"核心": JADE, "技能": ICE_BLUE, "记忆": AMBER, "语音": PURPLE, "屏幕操控": SILK}.get(toolset, MIST)

    for ts, items in groups.items():
        lines = []
        for t in items:
            emoji = t.get("emoji", "🔧")
            name = t.get("display_name", t.get("name", "?"))
            desc = t.get("description", "")[:40]
            lines.append(f"[{BRONZE}]{emoji} {name:<14}[/] [dim]{desc}[/]")
        table.add_row(f"[bold {JADE}]{ts}[/]", "\n".join(lines))
    console.print()
    console.print(Panel(table, border_style=BRONZE, box=box.ROUNDED,
                        title="🔧  工具清单", title_align="left", padding=(1, 2)))


def print_help_panel():
    """帮助面板 — 分组展示全部命令。"""
    from rich.table import Table
    table = Table(box=box.ROUNDED, border_style=BRONZE, show_header=False,
                  padding=(0, 1), expand=True)
    for group, cmds in _COMMANDS.items():
        lines = []
        for cmd, desc in cmds:
            lines.append(f"[bold {BRONZE}]{cmd:<16}[/] [dim]{desc}[/]")
        table.add_row(f"[bold {JADE}]{group}[/]", "\n".join(lines))
    console.print()
    console.print(Panel(table, border_style=BRONZE, box=box.ROUNDED,
                        title="⌨  命令列表", title_align="left", padding=(1, 2)))


def print_sessions_panel(sessions: list):
    """会话列表面板。"""
    if not sessions:
        console.print(Text("  无已保存的会话", style=MIST))
        return
    from rich.table import Table
    table = Table(box=box.SIMPLE, border_style=BRONZE_DIM, show_header=False,
                  padding=(0, 1))
    for s in sessions[:15]:
        sid = s.get("session_id", "")[:8]
        name = (s.get("name", "") or "")[:30]
        msgs = s.get("message_count", 0)
        table.add_row(
            f"[bold {BRONZE}][{escape(sid)}][/]",
            f"[{PARCHMENT}]{escape(name)}[/]",
            f"[dim]{msgs} 条消息[/]",
        )
    table.add_row("", f"[dim]输入 /load <id> 恢复会话[/]", "")
    console.print()
    console.print(Panel(table, border_style=BRONZE, box=box.ROUNDED,
                        title="📋  已保存会话", title_align="left", padding=(1, 2)))


def interactive_session_picker(sessions: list):
    """交互式会话选择器 — 打字筛选 + 方向键导航，Enter 加载，Esc 返回。

    Returns: 选中的 session_id，或 None
    """
    if not sessions:
        console.print(Text("  无已保存的会话", style=MIST))
        return None

    items = sessions[:15]

    console.print()
    filt = ""
    idx = 0

    def _filtered():
        if not filt:
            return items
        lo = filt.lower()
        return [s for s in items
                if lo in (s.get("name", "") or "").lower()
                or lo in s.get("session_id", "").lower()]

    def _build(sel, fstr, filtered):
        lines = []
        for i, s in enumerate(filtered):
            sid = s.get("session_id", "")[:8]
            name = (s.get("name", "") or "未命名")[:40]
            msgs = s.get("message_count", 0)
            line = f"  [{BRONZE}]{sid}[/]  {name}  [dim]({msgs} 条消息)[/]"
            if i == sel:
                lines.append(f" ▶ {line}")
            else:
                lines.append(f"   {line}")
        lines.append(f"\n [dim]↑↓ 选择   Enter 加载   Esc 返回[/]")
        lines.append(f"     [{JADE}]{fstr}▌[/]")
        body = Text.from_markup("\n".join(lines))
        return Panel(body, border_style=BRONZE, box=box.ROUNDED,
                     title="📋  已保存会话", title_align="left", padding=(1, 2))

    try:
        filtered = _filtered()
        with Live(_build(idx, filt, filtered), console=console, auto_refresh=False, transient=True) as live:
            while True:
                key = _getch()
                if key == '\x1b[A':  # Up
                    if filtered:
                        idx = (idx - 1) % len(filtered)
                elif key == '\x1b[B':  # Down
                    if filtered:
                        idx = (idx + 1) % len(filtered)
                elif key in ('\r', '\n'):  # Enter
                    return filtered[idx].get("session_id", "") if filtered else None
                elif key == '\x1b':  # Esc
                    return None
                elif key in ('\x7f', '\b'):  # Backspace
                    filt = filt[:-1]
                    filtered = _filtered()
                    idx = 0
                elif len(key) == 1 and key.isprintable():
                    filt += key
                    filtered = _filtered()
                    idx = 0
                else:
                    continue
                live.update(_build(idx, filt, filtered), refresh=True)
    except Exception:
        print_sessions_panel(sessions)
        return None


def interactive_model_picker(providers: dict, current_provider: str, current_model: str):
    """交互式模型选择器 — 打字筛选 + 方向键导航。

    Returns: (provider_name, model_key) 或 None
    """
    flat = []
    for pname, pinfo in providers.items():
        models = pinfo.get("models", {})
        for mkey, minfo in models.items():
            flat.append({
                "provider": pname,
                "model_key": mkey,
                "display": minfo.get("display_name", mkey),
                "api_name": minfo.get("name", mkey),
            })
    if not flat:
        return None

    console.print()
    filt = ""
    idx = 0

    def _filtered():
        if not filt:
            return flat
        lo = filt.lower()
        return [m for m in flat
                if lo in m["display"].lower()
                or lo in m["provider"].lower()
                or lo in m["model_key"].lower()]

    def _build(sel, fstr, items):
        lines = []
        for i, m in enumerate(items):
            tag = ""
            if m["provider"] == current_provider and m["model_key"] == current_model:
                tag = " [dim](当前)[/]"
            marker = " ▶" if i == sel else "  "
            lines.append(
                f"{marker} [{BRONZE}]{m['display']:<22}[/] "
                f"[dim]@{m['provider']}  {m['api_name']}[/]{tag}"
            )
        lines.append("\n [dim]↑↓ 选择   Enter 切换   Esc 返回[/]")
        lines.append(f"     [{JADE}]{fstr}▌[/]")
        body = Text.from_markup("\n".join(lines))
        return Panel(body, border_style=BRONZE, box=box.ROUNDED,
                     title="🔀  切换模型", title_align="left", padding=(1, 2))

    try:
        items = _filtered()
        with Live(_build(idx, filt, items), console=console, auto_refresh=False, transient=True) as live:
            while True:
                key = _getch()
                if key == '\x1b[A':
                    if items:
                        idx = (idx - 1) % len(items)
                elif key == '\x1b[B':
                    if items:
                        idx = (idx + 1) % len(items)
                elif key in ('\r', '\n'):
                    if items:
                        m = items[idx]
                        return (m["provider"], m["model_key"])
                    return None
                elif key == '\x1b':
                    return None
                elif key in ('\x7f', '\b'):
                    filt = filt[:-1]
                    items = _filtered()
                    idx = 0
                elif len(key) == 1 and key.isprintable():
                    filt += key
                    items = _filtered()
                    idx = 0
                else:
                    continue
                live.update(_build(idx, filt, items), refresh=True)
    except Exception:
        return None


def print_info_panel(title: str, text: str):
    """通用信息面板。"""
    console.print()
    console.print(Panel(
        Text(text, style=PARCHMENT),
        border_style=BRONZE_DIM,
        box=box.ROUNDED,
        title=title,
        title_align="left",
        padding=(1, 2),
    ))


def print_config_panel(config_data: dict):
    """配置面板 — 展示当前运行时配置。"""
    from rich.table import Table
    table = Table(box=box.SIMPLE, border_style=BRONZE_DIM, show_header=False,
                  padding=(0, 1))
    rows = [
        ("模型", config_data.get("model_display", "?")),
        ("Provider", config_data.get("provider", "?")),
        ("Base URL", config_data.get("base_url", "?")),
        ("最大轮次", str(config_data.get("max_turns", "?"))),
        ("超时", f"{config_data.get('timeout', 0)}s"),
        ("记忆系统", "开" if config_data.get("memory_enabled") else "关"),
        ("语音播报", "开" if config_data.get("auto_speak") else "关"),
        ("屏幕操控", "开" if config_data.get("computer_use") else "关"),
        ("日志级别", config_data.get("log_level", "?")),
    ]
    for label, value in rows:
        table.add_row(f"[bold {SILK}]{label}[/]", f"[{PARCHMENT}]{escape(str(value))}[/]")
    console.print()
    console.print(Panel(table, border_style=BRONZE, box=box.ROUNDED,
                        title="⚙  当前配置", title_align="left", padding=(1, 2)))


def print_banner(model: str):
    """开屏铭文 — 五星红旗 + 天工标识。"""
    console.print()

    RED  = "#DE2910"
    GOLD = "#FFDE00"

    # 五星红旗 — GB 12982-2004 精确制法，90×30 半高字符 (90×60 px)
    import math
    _fw, _fth = 90, 30
    _stars = [
        {'cx': 15, 'cy': 15, 'rx': 9, 'ry': 9, 'th': math.pi / 2},
        {'cx': 30, 'cy': 6,  'rx': 3, 'ry': 3, 'th': math.atan2(9, -15)},
        {'cx': 36, 'cy': 12, 'rx': 3, 'ry': 3, 'th': math.atan2(3, -21)},
        {'cx': 36, 'cy': 21, 'rx': 3, 'ry': 3, 'th': math.atan2(-6, -21)},
        {'cx': 30, 'cy': 27, 'rx': 3, 'ry': 3, 'th': math.atan2(-12, -15)},
    ]
    def _in_tri(px, py, a, b, c):
        d = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
        if abs(d) < 1e-10:
            return False
        a1 = ((b[1]-c[1])*(px-c[0]) + (c[0]-b[0])*(py-c[1])) / d
        b1 = ((c[1]-a[1])*(px-c[0]) + (a[0]-c[0])*(py-c[1])) / d
        c1 = 1 - a1 - b1
        return 0 <= a1 <= 1 and 0 <= b1 <= 1 and 0 <= c1 <= 1
    def _in_star(px, py, s):
        lx = (px - s['cx']) / s['rx']
        ly = -(py - s['cy']) / s['ry']
        r = (3 - math.sqrt(5)) / 2
        o, i = [], []
        for k in range(5):
            a = s['th'] + k * 2 * math.pi / 5
            o.append((math.cos(a), math.sin(a)))
            a = s['th'] + math.pi / 5 + k * 2 * math.pi / 5
            i.append((r * math.cos(a), r * math.sin(a)))
        sgn = None
        for k in range(5):
            cr = (i[(k+1)%5][0]-i[k][0])*(ly-i[k][1]) - (i[(k+1)%5][1]-i[k][1])*(lx-i[k][0])
            if abs(cr) < 1e-10:
                continue
            cu = cr > 0
            if sgn is None:
                sgn = cu
            elif sgn != cu:
                return False
        if sgn is not None:
            return True
        for k in range(5):
            if _in_tri(lx, ly, o[k], i[k], i[(k-1)%5]):
                return True
        return False
    def _pix(x, y):
        return any(_in_star(x + 0.5, y + 0.5, s) for s in _stars)
    for ty in range(_fth):
        t = Text()
        for tx in range(_fw):
            pt = _pix(tx, ty * 2)
            pb = _pix(tx, ty * 2 + 1)
            if pt and pb:
                t.append("█", style=f"bold {GOLD} on {GOLD}")
            elif not pt and not pb:
                t.append("█", style=f"bold {RED} on {RED}")
            elif pt and not pb:
                t.append("▀", style=f"bold {GOLD} on {RED}")
            else:
                t.append("▄", style=f"bold {GOLD} on {RED}")
        console.print(t)

    # 天工标识
    console.print()
    logo = Text()
    logo.append("  ◇  ", style=MIST)
    logo.append("天", style=f"bold {BRONZE}")
    logo.append("    工", style=f"bold {BRONZE}")
    logo.append("  ◇", style=MIST)
    console.print(logo)
    console.print(Text("  开  物  成  务", style=f"italic {SILK}"))

    # 品牌标语和模型信息
    motto = Text()
    motto.append(f"\n  {_MOTTO}", style=f"italic {MIST}")
    motto.append("\n\n")
    motto.append("  ─────────────────────────────\n", style=BRONZE_DIM)
    motto.append(f"  模型  {model}\n", style=SILK)
    motto.append("  ─────────────────────────────\n", style=BRONZE_DIM)
    console.print(motto)


def print_help_bar():
    """底部命令提示条。"""
    console.print(Text(
        " /help 命令  │  ! 执行  │  @ 引用文件  │  Ctrl+C 退出",
        style=MIST,
        justify="center",
    ))


# 工具调用静默计数器（跨轮次累积，最终一行带过）
_tool_tally = {}  # {display_name: [count, emoji, tool_name]}
_token_in = 0
_token_out = 0


def reset_tool_tally():
    """每次新用户输入时重置计数器。"""
    global _token_in, _token_out
    _tool_tally.clear()
    _token_in = 0
    _token_out = 0


def add_token_usage(prompt_tokens: int, completion_tokens: int):
    """跨轮次累积 token 用量。"""
    global _token_in, _token_out
    _token_in += prompt_tokens
    _token_out += completion_tokens


def print_tool_batch(calls: list):
    """静默累积工具调用计数，不打印。由 flush_tool_tally() 统一输出。

    calls: [(tool_name, display_name, emoji, args_dict), ...]
    """
    for tool_name, display_name, emoji, args in calls:
        if display_name in _tool_tally:
            _tool_tally[display_name][0] += 1
        else:
            _tool_tally[display_name] = [1, emoji, tool_name]


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def flush_tool_tally():
    """输出工具调用汇总 + token 消耗 — 一行带过，在最终回复之后作为脚注。"""
    global _token_in, _token_out
    if not _tool_tally and not _token_in:
        return

    text = Text("  ")
    first = True

    for display_name, (count, emoji, tool_name) in _tool_tally.items():
        color = _tool_color(tool_name)
        if not first:
            text.append("  │  ", style=MIST)
        first = False
        text.append(f"{emoji} {display_name}", style=color)
        if count > 1:
            text.append(f" ×{count}", style=color)

    if _token_in or _token_out:
        if not first:
            text.append("  │  ", style=MIST)
        text.append(f"↑ {_fmt_tokens(_token_in)}  ↓ {_fmt_tokens(_token_out)}", style=MIST)

    console.print(text)
    _tool_tally.clear()
    _token_in = 0
    _token_out = 0


def stream_assistant(stream):
    """流式输出 — Live Markdown 渲染，古铜色边框。"""
    console.print()  # 输入与回复之间留白
    md_buffer = ""
    first_chunk = True
    with Live(auto_refresh=False, console=console) as live:
        for chunk in stream:
            if first_chunk:
                # 首个 chunk 前显示"思考中"
                live.update(Panel(
                    Text("思考中...", style=MIST),
                    border_style=BRONZE,
                    box=box.HEAVY,
                    title="  天工",
                    title_align="left",
                    padding=(1, 2),
                ), refresh=True)
                first_chunk = False
            md_buffer += chunk
            try:
                live.update(Panel(
                    Markdown(md_buffer, code_theme="monokai"),
                    border_style=BRONZE,
                    box=box.HEAVY,
                    title="  天工",
                    title_align="left",
                    padding=(1, 2),
                ), refresh=True)
            except Exception:
                live.update(Panel(
                    Text(md_buffer, style=PARCHMENT),
                    border_style=BRONZE,
                    box=box.HEAVY,
                    title="  天工",
                    title_align="left",
                    padding=(1, 2),
                ), refresh=True)
    flush_tool_tally()  # 工具调用汇总脚注
    console.print()  # 回复后留白
    return md_buffer


def print_error(msg: str):
    """错误面板 — 朱砂色。"""
    console.print(Panel(
        Text(msg, style=PARCHMENT),
        border_style=VERMILLION,
        box=box.ROUNDED,
        title="✗  错误",
        title_align="left",
        padding=(0, 1),
    ))


def print_goodbye(session_name: str = "", turn_count: int = 0, memory_count: int = 0):
    """告别面板 — 附会话统计。"""
    body = Text()
    body.append("\n")
    body.append("   天工 · 后会有期", style=f"bold {BRONZE}")
    body.append("\n\n")
    if session_name:
        body.append(f"  会话  {session_name}\n", style=SILK)
    stats = []
    if turn_count:
        stats.append(f"对话 {turn_count} 轮")
    if memory_count:
        stats.append(f"记忆 {memory_count} 条")
    if stats:
        body.append(f"  {'  ·  '.join(stats)}\n", style=MIST)
    body.append("\n")

    console.print()
    console.print(Panel(body, border_style=BRONZE, box=box.HEAVY, padding=(1, 3)))
    console.print()


def print_memory(user_entries: list, memory_entries: list, facts: list):
    """记忆总览面板。"""
    if not user_entries and not memory_entries and not facts:
        console.print(Text("  暂无记忆", style=MIST))
        return

    parts = []
    if user_entries:
        parts.append(Text("📋 用户信息", style=f"bold {JADE}"))
        parts.append(Text("\n" + "\n".join(f"  {i}. {e}" for i, e in enumerate(user_entries, 1))))
        parts.append(Text("\n"))
    if memory_entries:
        parts.append(Text("🧠 对话记忆", style=f"bold {ICE_BLUE}"))
        parts.append(Text("\n" + "\n".join(f"  {i}. {e}" for i, e in enumerate(memory_entries, 1))))
        parts.append(Text("\n"))
    if facts:
        parts.append(Text("🔍 语义记忆", style=f"bold {AMBER}"))
        parts.append(Text("\n" + "\n".join(f"  - {f['content'][:120]}" for f in facts)))

    body = Text.assemble(*parts)
    console.print(Panel(
        body,
        border_style=BRONZE_DIM,
        box=box.ROUNDED,
        title="🧠  记忆库",
        title_align="left",
        padding=(1, 2),
    ))
