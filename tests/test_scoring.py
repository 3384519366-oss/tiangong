"""测试多信号检索评分: BM25 + 归一化 + 实体加权 + 融合。[借鉴mem0]"""

import pytest
from tiangong.memory.scoring import (
    bm25_score_single,
    bm25_score_document,
    normalize_bm25,
    calculate_entity_boost,
    fuse_scores,
    rank_results,
)


class TestBM25Single:
    def test_zero_tf(self):
        assert bm25_score_single(0, 10, 15.0, 3, 100) == 0.0

    def test_zero_df(self):
        assert bm25_score_single(5, 10, 15.0, 0, 100) == 0.0

    def test_zero_num_docs(self):
        assert bm25_score_single(5, 10, 15.0, 3, 0) == 0.0

    def test_normal_case(self):
        score = bm25_score_single(tf=3, doc_len=20, avg_doc_len=15.0,
                                  df=5, num_docs=100)
        assert score > 0.0

    def test_idf_rarer_term_scores_higher(self):
        """稀有词应获得更高 IDF 分数。"""
        common = bm25_score_single(3, 20, 15.0, df=50, num_docs=100)
        rare = bm25_score_single(3, 20, 15.0, df=2, num_docs=100)
        assert rare > common


class TestBM25Document:
    def test_basic(self):
        stats = {"df": {"hello": 3, "world": 5}, "num_docs": 100, "avg_doc_len": 10.0}
        score = bm25_score_document(
            ["hello", "world"], "hello world test", "doc1", stats
        )
        assert score > 0.0

    def test_no_match(self):
        stats = {"df": {}, "num_docs": 100, "avg_doc_len": 10.0}
        score = bm25_score_document(["xyz"], "hello world", "doc1", stats)
        assert score == 0.0


class TestNormalizeBM25:
    def test_short_query_steep(self):
        """短查询: 低分也能获得中等归一化值。"""
        norm = normalize_bm25(2.0, num_query_terms=1)
        assert 0.3 < norm < 0.7  # 陡峭 sigmoid 在低中点

    def test_long_query_flat(self):
        """长查询: 需要高分才能获得高归一化值。"""
        norm_low = normalize_bm25(2.0, num_query_terms=10)
        norm_high = normalize_bm25(10.0, num_query_terms=10)
        assert norm_high > norm_low

    def test_zero_terms(self):
        assert normalize_bm25(10.0, 0) == 0.0


class TestEntityBoost:
    def test_no_entities(self):
        boost = calculate_entity_boost([], [], {})
        assert boost == 0.0

    def test_match(self):
        boost = calculate_entity_boost(
            ["DeepSeek", "Agent"],
            [("DeepSeek", 0.9), ("Agent", 0.8)],
            {"DeepSeek": 1, "Agent": 2},
        )
        assert boost > 0.0

    def test_no_match(self):
        boost = calculate_entity_boost(
            ["Python"],
            [("DeepSeek", 0.9)],
            {},
        )
        assert boost == 0.0


class TestFuseScores:
    def test_semantic_only(self):
        combined, signals = fuse_scores(0.8)
        assert combined == 0.8
        assert "semantic" in signals

    def test_all_signals(self):
        combined, signals = fuse_scores(0.8, bm25=0.3, entity_boost=0.2)
        # (0.8 + 0.3 + 0.2) / 2.5 = 0.52
        assert combined < 0.8
        assert "bm25" in signals
        assert "entity" in signals

    def test_two_signals(self):
        combined, signals = fuse_scores(0.8, bm25=0.3)
        assert combined < 0.8
        assert "bm25" in signals


class TestRankResults:
    def test_empty(self):
        result = rank_results([], "test query")
        assert result == []

    def test_rank_preserves_order_by_fused_score(self):
        results = [
            {"id": "a", "content": "hello world", "score": 0.9},
            {"id": "b", "content": "something else", "score": 0.5},
        ]
        ranked = rank_results(results, "hello", corpus_stats={
            "df": {"hello": 1, "world": 1},
            "num_docs": 2,
            "avg_doc_len": 2.0,
        })
        assert len(ranked) > 0
        for i in range(len(ranked) - 1):
            assert ranked[i]["score"] >= ranked[i + 1]["score"]
