"""上下文压缩器 — token 估算 + 中间轮次摘要 + 工具结果裁剪。[CC]

当上下文超过 50% 窗口时触发压缩：
1. 工具结果裁剪（廉价预压缩）
2. LLM 摘要中间轮次
3. 保护系统提示 + 头尾轮次
4. 摘要注入带明确交接标记
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# DeepSeek V4 上下文窗口 128K
DEFAULT_CONTEXT_WINDOW = 128_000
COMPRESSION_THRESHOLD = 0.5

# 保护策略：保留 system + 前 N 轮 + 后 N 轮
PROTECT_HEAD_TURNS = 2
PROTECT_TAIL_TURNS = 3

# 工具结果裁剪阈值
MAX_TOOL_RESULT_CHARS = 2000
MAX_TOOL_RESULT_LINES = 50

# 摘要提示词
_SUMMARIZE_PROMPT = """请用中文简要总结以下对话的关键信息，保留:
1. 用户的核心请求和意图
2. 已完成的操作和结果
3. 重要的技术决策和发现
4. 当前进行中的任务状态

只输出摘要，不要加任何前缀或解释:"""


def estimate_tokens(text: str) -> int:
    """估算 token 数。中文 ≈ 0.5 token/字，英文 ≈ 0.25 token/字。"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿' or '　' <= c <= '〿')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 0.5 + max(other_chars * 0.25, 1))


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                total += estimate_tokens(json.dumps(tc.get("function", {}), ensure_ascii=False))
        total += 12  # role + 格式开销
    return total


def prune_tool_result(content: str) -> str:
    """裁剪单个工具结果：保留头尾，中间截断。"""
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content

    lines = content.split("\n")
    if len(lines) <= MAX_TOOL_RESULT_LINES:
        return content[:MAX_TOOL_RESULT_CHARS] + "\n... (已截断)"

    head = lines[:MAX_TOOL_RESULT_LINES // 2]
    tail = lines[-(MAX_TOOL_RESULT_LINES // 2):]
    omitted = len(lines) - MAX_TOOL_RESULT_LINES
    return (
        "\n".join(head)
        + f"\n... ({omitted} 行已省略) ...\n"
        + "\n".join(tail)
    )


def _find_turn_boundaries(messages: List[Dict[str, Any]]) -> List[int]:
    """找到每个 user 轮次的起始索引。"""
    return [i for i, msg in enumerate(messages) if msg.get("role") == "user"]


class ContextCompressor:
    """上下文压缩器。[CC]

    策略:
    1. 工具结果裁剪 — 廉价预压缩，裁剪过长的工具输出
    2. 轮次边界检测 — 按 user 消息切分轮次
    3. 头尾保护 — system + 前2轮 + 后3轮不参与摘要
    4. LLM 摘要 — 中间轮次压缩为摘要段落
    """

    def __init__(self, context_window: int = DEFAULT_CONTEXT_WINDOW):
        self.context_window = context_window
        self.threshold = int(context_window * COMPRESSION_THRESHOLD)
        self._compression_count = 0
        self._pruned_chars = 0
        self._summarized_turns = 0

    @property
    def stats(self) -> dict:
        return {
            "compression_count": self._compression_count,
            "pruned_chars": self._pruned_chars,
            "summarized_turns": self._summarized_turns,
        }

    def needs_compression(self, messages: List[Dict[str, Any]]) -> bool:
        """检查是否超过阈值。"""
        return estimate_messages_tokens(messages) > self.threshold

    def get_usage_ratio(self, messages: List[Dict[str, Any]]) -> float:
        """当前上下文使用率 (0.0 ~ 1.0+)。"""
        return estimate_messages_tokens(messages) / self.context_window

    def compress(
        self,
        messages: List[Dict[str, Any]],
        llm_summarize=None,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """压缩消息列表，返回 (消息列表, 是否压缩了)。

        llm_summarize: 可选的外部摘要函数 (messages) -> str
        """
        if not messages:
            return messages, False

        original_count = len(messages)

        # Step 1: 工具结果裁剪
        messages = self._prune_tool_results(messages)

        # Step 2: 裁剪后检查是否仍需压缩
        if not self.needs_compression(messages):
            return messages, len(messages) < original_count

        # Step 3: 轮次边界
        boundaries = _find_turn_boundaries(messages)
        total_turns = len(boundaries)

        if total_turns <= PROTECT_HEAD_TURNS + PROTECT_TAIL_TURNS:
            logger.debug("轮次太少 (%d)，跳过摘要压缩", total_turns)
            return messages, len(messages) < original_count

        # Step 4: 分割
        system_msgs = [m for m in messages if m.get("role") == "system"]
        sys_count = len(system_msgs)

        head_end = boundaries[PROTECT_HEAD_TURNS]
        tail_start = boundaries[-(PROTECT_TAIL_TURNS)]

        head_msgs = messages[sys_count:head_end]
        middle_msgs = messages[head_end:tail_start]
        tail_msgs = messages[tail_start:]

        if not middle_msgs:
            return messages, False

        # Step 5: 摘要
        if llm_summarize:
            try:
                summary = llm_summarize(middle_msgs)
            except Exception as e:
                logger.warning("LLM 摘要失败: %s，使用基础摘要", e)
                summary = self._basic_summary(middle_msgs)
        else:
            summary = self._basic_summary(middle_msgs)

        self._compression_count += 1
        self._summarized_turns += total_turns - PROTECT_HEAD_TURNS - PROTECT_TAIL_TURNS

        # Step 6: 重组
        compressed = list(system_msgs)
        compressed.extend(head_msgs)
        compressed.append({
            "role": "user",
            "content": (
                f"[第 {self._compression_count} 次上下文压缩]\n"
                f"以下是对之前 {total_turns - PROTECT_HEAD_TURNS - PROTECT_TAIL_TURNS} 轮对话的摘要:\n\n"
                f"{summary}\n\n"
                f"请基于以上摘要继续工作。如需详细信息，可重新查询或读取文件。"
            ),
        })
        compressed.extend(tail_msgs)

        logger.info(
            "上下文已压缩: %d → %d 条消息 (%d轮 → 保留头%d尾%d, 摘要%d轮)",
            original_count, len(compressed), total_turns,
            PROTECT_HEAD_TURNS, PROTECT_TAIL_TURNS,
            total_turns - PROTECT_HEAD_TURNS - PROTECT_TAIL_TURNS,
        )

        return compressed, True

    def _prune_tool_results(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """裁剪过长的工具结果。"""
        result = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "") or ""
                pruned = prune_tool_result(content)
                if pruned != content:
                    self._pruned_chars += len(content) - len(pruned)
                    msg = dict(msg)
                    msg["content"] = pruned
            result.append(msg)
        return result

    def _basic_summary(self, messages: List[Dict[str, Any]]) -> str:
        """基础摘要（无 LLM 时降级使用）。提取关键信息。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "")
            content = (msg.get("content", "") or "")[:200]
            if not content.strip():
                continue
            if role == "user":
                lines.append(f"- 用户请求: {content}")
            elif role == "assistant":
                lines.append(f"- 助手回应: {content}")
            elif role == "tool":
                lines.append(f"- 工具结果: {content[:100]}")
        return "\n".join(lines) if lines else "（对话中）"


def llm_summarize_messages(
    messages: List[Dict[str, Any]],
    client,
    model: str = "",
    max_tokens: int = 4096,
) -> str:
    """使用 LLM 摘要对话。同步调用。"""
    text_parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                text_parts.append(f"[调用工具: {fn.get('name', '')}]")
        if content.strip():
            text_parts.append(f"[{role}]: {content[:800]}")

    conversation = "\n".join(text_parts)
    if len(conversation) > 12000:
        conversation = conversation[:12000] + "\n... (已截断)"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SUMMARIZE_PROMPT},
            {"role": "user", "content": f"请总结以下对话:\n\n{conversation}"},
        ],
        max_tokens=min(max_tokens, 4096),
    )
    return response.choices[0].message.content or ""
