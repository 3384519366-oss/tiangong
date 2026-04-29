"""ChromaDB + SQLite storage layer for permanent memory.

Three tiers:
- ChromaDB: semantic/vector memory with embeddings
- SQLite: episodic memory (conversation summaries, timestamps)
- File: existing MEMORY.md/USER.md for backward compat / quick access

[mem0 升级] 新增:
- BM25 语料统计 + 关键词搜索
- 实体提取 + 实体→文档索引
- MD5 哈希去重
- 多信号融合检索 (semantic + BM25 + entity boost)
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from tiangong.core.config import Config

logger = logging.getLogger(__name__)

# 检索配置
SEMANTIC_OVERFETCH = 4    # 语义搜索 4x 过取
MIN_FETCH = 60            # 至少取 60 个候选


class MemoryStore:
    """Permanent memory with vector search and relational storage.

    [mem0 升级] 多信号融合检索 + BM25 + 实体加权 + 去重
    """

    def __init__(self):
        config = Config.get()
        data_dir = config.get_memory_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        # ChromaDB for semantic memory
        chroma_dir = str(data_dir / "chromadb")
        self._chroma_client = chromadb.PersistentClient(
            path=chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._semantic = self._chroma_client.get_or_create_collection(
            name="tiangong_memories",
            metadata={"hnsw:space": "cosine"},
        )

        # [mem0] 实体存储 — 独立的 ChromaDB collection
        self._entities = self._chroma_client.get_or_create_collection(
            name="tiangong_entities",
            metadata={"hnsw:space": "cosine"},
        )

        # SQLite for episodic memory
        self._db_path = data_dir / "episodic.db"
        self._init_db()

        # [mem0] BM25 语料统计（内存中维护）
        self._bm25_stats = {
            "num_docs": 0,
            "total_doc_len": 0,
            "avg_doc_len": 0.0,
            "df": {},  # {term: document_frequency}
        }

        # [mem0] 实体索引 {entity_name: [(doc_id, confidence), ...]}
        self._entity_index: Dict[str, list] = {}

        # [mem0] 去重管理
        from .dedup import get_dedup
        self._dedup = get_dedup()

        # 初始化统计和索引
        self._rebuild_stats()

    def _rebuild_stats(self):
        """从现有 ChromaDB 数据重建 BM25 统计和实体索引（最多处理 20000 条）。"""
        try:
            all_data = self._semantic.get(limit=20000)
            if not all_data or not all_data.get("ids"):
                return

            ids = all_data["ids"]
            docs = all_data.get("documents", []) or []
            metas = all_data.get("metadatas", []) or []

            from .scoring import _tokenize
            from .entity_extraction import extract_entities

            num_docs = 0
            total_len = 0
            df = {}

            for i, doc_id in enumerate(ids):
                content = docs[i] if i < len(docs) else ""
                if not content:
                    continue

                num_docs += 1
                tokens = _tokenize(content)
                total_len += len(tokens)

                # DF 统计
                unique_terms = set(tokens)
                for term in unique_terms:
                    df[term] = df.get(term, 0) + 1

                # 实体索引
                entities = extract_entities(content)
                for e in entities:
                    if e.name not in self._entity_index:
                        self._entity_index[e.name] = []
                    self._entity_index[e.name].append((doc_id, e.confidence))

                # 加载到去重池
                self._dedup.add_hash(content)

            self._bm25_stats = {
                "num_docs": num_docs,
                "total_doc_len": total_len,
                "avg_doc_len": total_len / max(num_docs, 1),
                "df": df,
            }

            logger.info("BM25/实体索引已重建: %d 文档, %d 实体",
                        num_docs, len(self._entity_index))

        except Exception as e:
            logger.warning("重建统计失败: %s", e)

    def _update_bm25_stats(self, content: str):
        """增量更新 BM25 语料统计。"""
        from .scoring import _tokenize

        tokens = _tokenize(content)
        self._bm25_stats["num_docs"] += 1
        self._bm25_stats["total_doc_len"] += len(tokens)
        self._bm25_stats["avg_doc_len"] = (
            self._bm25_stats["total_doc_len"] / max(self._bm25_stats["num_docs"], 1)
        )

        for term in set(tokens):
            self._bm25_stats["df"][term] = self._bm25_stats["df"].get(term, 0) + 1

    def _update_entity_index(self, doc_id: str, content: str):
        """增量更新实体索引。"""
        from .entity_extraction import extract_entities

        entities = extract_entities(content)
        for e in entities:
            if e.name not in self._entity_index:
                self._entity_index[e.name] = []
            self._entity_index[e.name].append((doc_id, e.confidence))

    def _init_db(self):
        """Create SQLite tables for episodic memory."""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    facts_extracted TEXT,
                    created_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL UNIQUE,
                    category TEXT DEFAULT 'general',
                    importance REAL DEFAULT 0.5,
                    created_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed REAL,
                    content_hash TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            # 迁移：旧版 facts 表可能缺少 content_hash 列
            try:
                conn.execute("ALTER TABLE facts ADD COLUMN content_hash TEXT")
            except Exception:
                pass
            conn.commit()

    # ── Semantic Memory (ChromaDB) ──────────────────────────────

    def add_semantic(self, content: str, metadata: Dict[str, Any] | None = None,
                     doc_id: str | None = None):
        """Add a memory entry to ChromaDB for vector search.

        [mem0] 新增去重检查
        """
        if doc_id is None:
            import uuid
            doc_id = f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

        # [mem0] 去重检查
        is_dup, reason = self._dedup.is_duplicate(content)
        if is_dup:
            logger.debug("跳过重复记忆 (%.20s...): %s", content, reason)
            return

        meta = metadata or {}
        meta.setdefault("created_at", time.time())
        meta.setdefault("category", "general")

        # 提取实体写入 metadata
        from .entity_extraction import extract_entities
        entities = extract_entities(content)
        if entities:
            meta["entities"] = [e.name for e in entities[:10]]
            meta["entity_types"] = [e.type for e in entities[:10]]

        self._semantic.add(
            documents=[content],
            metadatas=[meta],
            ids=[doc_id],
        )

        # [mem0] 增量更新统计
        self._update_bm25_stats(content)
        self._update_entity_index(doc_id, content)
        self._dedup.add_hash(content)

        logger.debug("Semantic memory added: %s (entities: %d)", doc_id, len(entities))

    def search_semantic(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Search semantic memory by vector similarity.

        [mem0] 多信号融合重排
        """
        total = self._semantic.count()
        if total == 0:
            return []

        # 4x 过取
        fetch_n = max(n_results * SEMANTIC_OVERFETCH, MIN_FETCH)
        fetch_n = min(fetch_n, total)

        results = self._semantic.query(
            query_texts=[query],
            n_results=fetch_n,
        )

        items = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                items.append({
                    "id": doc_id,
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0,
                    "score": 1.0 - (results["distances"][0][i] / 2.0) if results["distances"] else 0.5,
                })

        # [mem0] 多信号融合重排
        from .scoring import rank_results
        items = rank_results(items, query,
                            corpus_stats=self._bm25_stats,
                            entity_index=self._entity_index)

        return items[:n_results]

    def delete_semantic(self, doc_id: str):
        """Remove a semantic memory entry."""
        self._semantic.delete(ids=[doc_id])

    # ── Episodic Memory (SQLite) ────────────────────────────────

    def add_episode(self, session_id: str, summary: str, facts: List[str] | None = None):
        """Store a session summary as episodic memory."""
        now = time.time()
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO episodes (session_id, summary, facts_extracted, created_at) VALUES (?, ?, ?, ?)",
                (session_id, summary, json.dumps(facts or []), now),
            )
            conn.commit()
        logger.debug("Episode added for session %s", session_id)

    def search_episodes(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Simple keyword search across episode summaries."""
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT id, session_id, summary, facts_extracted, created_at, access_count "
                "FROM episodes WHERE summary LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()

        results = []
        for row in rows:
            conn.execute("UPDATE episodes SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                         (time.time(), row[0]))
            results.append({
                "id": row[0], "session_id": row[1], "summary": row[2],
                "facts": json.loads(row[3]) if row[3] else [],
                "created_at": row[4], "access_count": row[5] + 1,
            })
        if results:
            conn.commit()
        return results

    def get_recent_episodes(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most recent episode summaries."""
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT id, session_id, summary, facts_extracted, created_at "
                "FROM episodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "summary": r[2],
             "facts": json.loads(r[3]) if r[3] else [], "created_at": r[4]}
            for r in rows
        ]

    # ── Fact Storage ────────────────────────────────────────────

    def add_fact(self, content: str, category: str = "general", importance: float = 0.5):
        """Store a discrete fact. [mem0] 去重增强"""
        now = time.time()
        from .dedup import hash_content
        content_hash = hash_content(content)

        with sqlite3.connect(str(self._db_path)) as conn:
            try:
                conn.execute(
                    "INSERT INTO facts (content, category, importance, created_at, content_hash) VALUES (?, ?, ?, ?, ?)",
                    (content, category, importance, now, content_hash),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Fact already exists
                conn.execute(
                    "UPDATE facts SET access_count = access_count + 1, last_accessed = ? WHERE content = ?",
                    (now, content),
                )
                conn.commit()

    def search_facts(self, query: str = "", category: str | None = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Search facts by keyword and/or category."""
        with sqlite3.connect(str(self._db_path)) as conn:
            if query:
                rows = conn.execute(
                    "SELECT id, content, category, importance, created_at, access_count "
                    "FROM facts WHERE content LIKE ? "
                    + ("AND category = ?" if category else "")
                    + " ORDER BY importance DESC, access_count DESC LIMIT ?",
                    (f"%{query}%",) + ((category, limit) if category else (limit,)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, category, importance, created_at, access_count "
                    "FROM facts "
                    + ("WHERE category = ?" if category else "")
                    + " ORDER BY importance DESC, access_count DESC LIMIT ?",
                    ((category, limit) if category else (limit,)),
                ).fetchall()
        return [
            {"id": r[0], "content": r[1], "category": r[2],
             "importance": r[3], "created_at": r[4], "access_count": r[5]}
            for r in rows
        ]

    # ── Session Logging ─────────────────────────────────────────

    def log_message(self, session_id: str, role: str, content: str):
        """Log a conversation message for later consolidation."""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO session_log (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, time.time()),
            )
            conn.commit()

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all messages from a session for consolidation."""
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM session_log WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    # ── Hybrid Search [mem0 升级] ───────────────────────────────

    def hybrid_search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Search across semantic, episodic, and fact stores.

        [mem0] 语义结果使用多信号融合
        """
        semantic = self.search_semantic(query, n_results=limit)
        episodes = self.search_episodes(query, limit=limit)
        facts = self.search_facts(query, limit=limit)

        # 添加融合信号说明
        return {
            "semantic": semantic,
            "episodes": episodes,
            "facts": facts,
            "_search_info": {
                "bm25_docs": self._bm25_stats.get("num_docs", 0),
                "entities": len(self._entity_index),
                "dedup_hashes": self._dedup.get_stats(),
            },
        }

    def get_context_for_prompt(self, query: str, max_chars: int = 3000) -> str:
        """Retrieve relevant memories formatted for system prompt injection.

        [mem0] 使用多信号融合检索
        """
        results = self.hybrid_search(query, limit=5)
        parts = []

        if results["facts"]:
            facts_text = "\n".join(f"- {f['content']}" for f in results["facts"][:5])
            parts.append(f"═══ RELEVANT FACTS ═══\n{facts_text}")

        if results["semantic"]:
            mem_lines = []
            for m in results["semantic"][:3]:
                score = m.get("score", 0)
                signals = m.get("_signals", "semantic")
                mem_lines.append(f"- [{signals}|{score:.2f}] {m['content'][:200]}")
            parts.append(f"═══ PAST MEMORIES ═══\n" + "\n".join(mem_lines))

        if results["episodes"]:
            ep_text = "\n".join(f"- [{e['created_at']:.0f}] {e['summary'][:200]}" for e in results["episodes"][:3])
            parts.append(f"═══ PAST SESSIONS ═══\n{ep_text}")

        combined = "\n\n".join(parts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n... (truncated)"
        return combined

    # ── 统计查询 [mem0] ─────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取记忆系统统计。"""
        return {
            "semantic_docs": self._semantic.count(),
            "entities": len(self._entity_index),
            "bm25": self._bm25_stats,
            "dedup": self._dedup.get_stats(),
        }


# Singleton
_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
