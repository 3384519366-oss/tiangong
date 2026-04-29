"""Memory retriever — builds context for system prompt injection."""

import logging
from typing import Dict, List, Optional

from .store import get_store
from tiangong.core.config import Config

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """Retrieves relevant memories for session context."""

    def __init__(self):
        self.store = get_store()

    def get_context(self, query: str = "", max_chars: int = 3000) -> str:
        """Get relevant memories for system prompt context."""
        if not query:
            # Get recent episodes and high-importance facts
            return self._get_default_context(max_chars)
        return self.store.get_context_for_prompt(query, max_chars)

    def _get_default_context(self, max_chars: int = 3000) -> str:
        """Get default context (recent + important) when no query."""
        episodes = self.store.get_recent_episodes(limit=3)
        facts = self.store.search_facts(limit=10)

        parts = []

        if facts:
            important = sorted(facts, key=lambda f: (f["importance"], f["access_count"]), reverse=True)
            facts_text = "\n".join(f"- {f['content']}" for f in important[:8])
            parts.append(f"═══ REMEMBERED FACTS ═══\n{facts_text}")

        if episodes:
            ep_text = "\n".join(
                f"- [{self._format_time(e['created_at'])}] {e['summary'][:150]}"
                for e in episodes
            )
            parts.append(f"═══ RECENT SESSIONS ═══\n{ep_text}")

        combined = "\n\n".join(parts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n..."
        return combined

    @staticmethod
    def _format_time(ts: float) -> str:
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        diff = now - dt
        if diff.days == 0:
            return f"今天 {dt.strftime('%H:%M')}"
        elif diff.days == 1:
            return f"昨天 {dt.strftime('%H:%M')}"
        elif diff.days < 7:
            return f"{diff.days}天前"
        return dt.strftime("%m/%d")
