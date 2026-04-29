"""Memory consolidation — runs after sessions to extract and index key info.

Uses the LLM to summarize conversations, extract facts, and store them
in ChromaDB (semantic) + SQLite (episodic/facts).
"""

import json
import logging
import time
import uuid
from typing import Any, Dict, List

from tiangong.core.config import Config
from tiangong.core.llm_client import LLMClient
from .store import get_store

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """You are a memory consolidator. Review the conversation below and extract:

1. **summary**: A 2-3 sentence summary of what happened in this session.
2. **facts**: A list of discrete facts about the user or environment that would be useful to remember in future sessions. Focus on:
   - User preferences, habits, and personal details
   - Environment facts (OS, tools, project structure)
   - Decisions made and their reasons
   - Problems solved and how
   - Things the user explicitly asked to remember

Return ONLY valid JSON:
{
  "summary": "...",
  "facts": ["fact 1", "fact 2", ...]
}

Conversation:
"""


class MemoryConsolidator:
    """Summarizes conversations and indexes memories after each session."""

    def __init__(self):
        self.llm = LLMClient()

    def consolidate(self, session_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract summary and facts from session messages, store in all tiers."""
        # Build conversation text for the LLM
        convo = "\n".join(
            f"[{m['role']}]: {str(m.get('content', ''))[:500]}"
            for m in messages
            if m.get('content')
        )

        if len(convo) < 100:
            logger.debug("Session %s too short to consolidate", session_id)
            return {"consolidated": False, "reason": "too_short"}

        # If conversation too long, truncate
        if len(convo) > 8000:
            convo = convo[:4000] + "\n...\n" + convo[-4000:]

        # Ask LLM to extract summary and facts
        try:
            response = self.llm.chat([
                {"role": "user", "content": CONSOLIDATION_PROMPT + convo}
            ])
            content = response.get("content", "").strip()
            # Extract JSON from response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            data = json.loads(content)
        except KeyboardInterrupt:
            raise
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Consolidation failed: %s", e)
            # Fallback: just store a basic summary
            data = {"summary": convo[:500], "facts": []}

        store = get_store()
        summary = data.get("summary", "")
        facts = data.get("facts", [])

        # Store episode
        store.add_episode(session_id, summary, facts)

        # Store facts
        for fact in facts:
            store.add_fact(fact)
            store.add_semantic(fact, metadata={"category": "extracted_fact"})

        # Store summary as semantic memory too
        if summary:
            store.add_semantic(summary, metadata={"category": "session_summary", "session_id": session_id})

        logger.info("Consolidated session %s: %d facts extracted", session_id, len(facts))
        return {
            "consolidated": True,
            "summary": summary,
            "facts": facts,
            "fact_count": len(facts),
        }


def consolidate_session(session_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convenience function to consolidate a session."""
    consolidator = MemoryConsolidator()
    return consolidator.consolidate(session_id, messages)
