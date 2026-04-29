"""多信号检索评分 — BM25归一化 + 实体加权 + 加法融合。[借鉴mem0]

借鉴 mem0/memory/utils/scoring.py:
- BM25 原始分 sigmoid 归一化到 [0,1]
- 查询长度自适应参数
- 加法融合: (semantic + bm25 + entity_boost) / divisor
- 除数根据活跃信号数量自适应
"""

import math
from typing import Dict, List, Tuple

# BM25 参数
BM25_K1 = 1.5      # 词频饱和度
BM25_B = 0.75       # 文档长度归一化
BM25_MAX_RAW = 50   # BM25 理论最大原始分（用于 sigmoid 截断）

# 实体加权参数
ENTITY_BOOST_WEIGHT = 0.5
ENTITY_MATCH_THRESHOLD = 0.5
MAX_QUERY_ENTITIES = 8

# 分数融合参数
MIN_SEMANTIC_THRESHOLD = 0.3  # 语义分低于此的直接淘汰


def bm25_score_single(
    tf: int,             # 词频
    doc_len: int,        # 文档长度
    avg_doc_len: float,  # 平均文档长度
    df: int,             # 文档频率（包含该词的文档数）
    num_docs: int,       # 总文档数
) -> float:
    """计算单个词对单个文档的 BM25 分数。"""
    if num_docs <= 0 or df <= 0 or tf <= 0:
        return 0.0

    # IDF
    idf = math.log(1.0 + (num_docs - df + 0.5) / (df + 0.5))

    # TF 饱和度
    tf_norm = (tf * (BM25_K1 + 1.0)) / (tf + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / avg_doc_len))

    return idf * tf_norm


def bm25_score_document(
    query_terms: List[str],
    doc_text: str,
    doc_id: str,
    doc_stats: dict,
) -> float:
    """计算查询对单个文档的完整 BM25 分数。"""
    score = 0.0
    doc_terms = _tokenize(doc_text)
    doc_len = len(doc_terms)

    for term in query_terms:
        tf = doc_terms.count(term)
        if tf == 0:
            continue
        df = doc_stats.get("df", {}).get(term, 1)
        num_docs = doc_stats.get("num_docs", 1)
        avg_dl = doc_stats.get("avg_doc_len", doc_len or 1)
        score += bm25_score_single(tf, doc_len, avg_dl, df, num_docs)

    return score


def normalize_bm25(raw_score: float, num_query_terms: int, max_raw: float = BM25_MAX_RAW) -> float:
    """Sigmoid 归一化 BM25 原始分到 [0, 1]。

    借鉴 mem0: 查询越长，sigmoid 中点越大、越平缓。
    """
    if num_query_terms <= 0:
        return 0.0

    # 自适应参数
    if num_query_terms <= 2:
        # 短查询：陡峭 sigmoid，低中点 -> 即使低分也能得到权值
        midpoint = 2.0
        steepness = 1.5
    elif num_query_terms <= 5:
        midpoint = 4.0
        steepness = 1.0
    else:
        # 长查询：缓坡 sigmoid，高中点 -> 需要更高分才能得到权值
        midpoint = 8.0
        steepness = 0.5

    # Clip raw score
    x = min(raw_score, max_raw)

    # Sigmoid
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def calculate_entity_boost(
    doc_entities: List[str],
    query_entities: List[Tuple[str, float]],
    entity_link_counts: Dict[str, int],
) -> float:
    """计算实体加权 boost 值。

    借鉴 mem0: boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
    """
    if not query_entities or not doc_entities:
        return 0.0

    boost = 0.0
    doc_entity_set = set(e.lower() for e in doc_entities)

    for entity, confidence in query_entities[:MAX_QUERY_ENTITIES]:
        if entity.lower() not in doc_entity_set:
            continue
        if confidence < ENTITY_MATCH_THRESHOLD:
            continue

        # 实体关联的记忆数越多，单次 boost 越小（稀释效应）
        link_count = entity_link_counts.get(entity, 1)
        memory_count_weight = 1.0 / math.sqrt(max(link_count, 1))

        boost += confidence * ENTITY_BOOST_WEIGHT * memory_count_weight

    return min(boost, 1.5)  # 上限 1.5


def fuse_scores(
    semantic: float,
    bm25: float = 0.0,
    entity_boost: float = 0.0,
) -> Tuple[float, str]:
    """融合多信号分数。

    借鉴 mem0: additive fusion with adaptive divisor

    返回: (融合分, 信号说明)
    """
    active_signals = ["semantic"]
    raw = semantic

    if bm25 > 0.001:
        raw += bm25
        active_signals.append("bm25")

    if entity_boost > 0.001:
        raw += entity_boost
        active_signals.append("entity")

    # 自适应除数
    if len(active_signals) == 3:
        divisor = 2.5
    elif len(active_signals) == 2:
        divisor = 2.0
    else:
        divisor = 1.0

    combined = min(raw / divisor, 1.0)

    return combined, "+".join(active_signals)


def rank_results(
    results: List[dict],
    query: str,
    corpus_stats: dict = None,
    entity_index: dict = None,
) -> List[dict]:
    """对搜索结果进行多信号重排。

    results: [{id, content, score (语义相似度), metadata, ...}]
    query: 查询文本
    corpus_stats: 语料统计 {df: {term: count}, num_docs: int, avg_doc_len: float}
    entity_index: 实体索引 {entity: [(doc_id, confidence), ...]}

    返回: 重排后的结果，score 替换为融合分
    """
    if not results:
        return results

    query_terms = _tokenize(query)
    num_terms = len(query_terms)

    # 提取查询实体
    query_entities = []
    if entity_index:
        from .entity_extraction import extract_entities
        entities = extract_entities(query)
        query_entities = [(e.name, e.confidence) for e in entities]

    # 实体链接计数
    entity_link_counts = {}
    if entity_index:
        for ent, _ in query_entities:
            entity_link_counts[ent] = len(entity_index.get(ent, []))

    corpus_stats = corpus_stats or {}
    ranked = []

    for item in results:
        semantic_score = item.get("score", item.get("distance", 0.5))
        # 距离转相似度（cosine distance → similarity）
        if isinstance(semantic_score, (int, float)) and 0 <= semantic_score <= 2:
            semantic_score = 1.0 - semantic_score / 2.0

        content = item.get("content", "") or ""

        # BM25 分数
        bm25 = 0.0
        if query_terms and corpus_stats:
            bm25_raw = bm25_score_document(query_terms, content,
                                           item.get("id", ""), corpus_stats)
            bm25 = normalize_bm25(bm25_raw, num_terms)

        # 实体 boost
        entity_boost = 0.0
        if entity_index and query_entities:
            doc_entities = _get_doc_entities(item.get("id", ""),
                                             item.get("metadata", {}),
                                             entity_index)
            entity_boost = calculate_entity_boost(doc_entities, query_entities,
                                                  entity_link_counts)

        # 语义分阈值
        if semantic_score < MIN_SEMANTIC_THRESHOLD and bm25 < 0.01:
            continue

        combined, signals = fuse_scores(semantic_score, bm25, entity_boost)

        new_item = dict(item)
        new_item["score"] = combined
        new_item["_signals"] = signals
        new_item["_semantic_raw"] = round(semantic_score, 4)
        if bm25 > 0.001:
            new_item["_bm25"] = round(bm25, 4)
        if entity_boost > 0.001:
            new_item["_entity_boost"] = round(entity_boost, 4)
        ranked.append(new_item)

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def apply_time_decay(
    results: List[dict],
    current_time: float = None,
    half_life_days: float = 30.0,
    min_decay: float = 0.2,
) -> List[dict]:
    """对搜索结果应用时间衰减。

    记忆重要性随时间指数衰减: decay = max(2^(-age/half_life), min_decay)
    - half_life_days: 半衰期（天），默认 30 天
    - min_decay: 最小衰减因子（保留的最低调权），默认 0.2

    结果按衰减后的分数重新排序。
    """
    import time as _time
    now = current_time or _time.time()
    half_life = half_life_days * 86400  # 转换为秒

    for item in results:
        created_at = item.get("created_at") or item.get("metadata", {}).get("created_at")
        if not created_at:
            continue

        # 支持 ISO 格式字符串
        if isinstance(created_at, str):
            try:
                from datetime import datetime
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                continue

        age_seconds = max(0, now - created_at)
        decay = max(2.0 ** (-age_seconds / half_life), min_decay)

        old_score = item.get("score", item.get("distance", 0.5))
        if isinstance(old_score, (int, float)):
            item["score"] = old_score * decay
            item["_decay"] = round(decay, 4)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def _tokenize(text: str) -> List[str]:
    """简单分词：中文字符级 + 英文词级。"""
    import re
    tokens = []
    # 中文按单字
    chinese = re.findall(r'[一-鿿]', text)
    tokens.extend(chinese)
    # 英文按词
    english = re.findall(r'[a-zA-Z0-9]+', text.lower())
    tokens.extend(english)
    return tokens


def _get_doc_entities(doc_id: str, metadata: dict,
                      entity_index: dict) -> List[str]:
    """获取文档关联的实体列表。"""
    entities = metadata.get("entities", [])
    if not entities:
        # 从实体索引反查
        entities = []
        for ent, docs in entity_index.items():
            for doc_ref in docs:
                if isinstance(doc_ref, tuple) and doc_ref[0] == doc_id:
                    entities.append(ent)
                elif isinstance(doc_ref, str) and doc_ref == doc_id:
                    entities.append(ent)
    return entities
