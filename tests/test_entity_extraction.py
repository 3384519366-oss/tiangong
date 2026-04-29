"""测试实体提取: 中文+英文 4 类实体识别。[借鉴mem0]"""

import pytest
from tiangong.memory.entity_extraction import (
    Entity,
    extract_entities,
    build_entity_index,
    ENTITY_PROPER,
    ENTITY_QUOTED,
    ENTITY_COMPOUND,
    ENTITY_NOUN,
)


class TestExtractEntities:
    def test_quoted_entities(self):
        entities = extract_entities('请读取"config.yaml"和"agent.py"')
        quoted = [e for e in entities if e.type == ENTITY_QUOTED]
        names = {e.name for e in quoted}
        assert "config.yaml" in names
        assert "agent.py" in names

    def test_proper_nouns(self):
        entities = extract_entities("使用 DeepSeek 和 OpenAI 的 API")
        proper = [e for e in entities if e.type == ENTITY_PROPER]
        names = {e.name for e in proper}
        assert "DeepSeek" in names
        assert "OpenAI" in names

    def test_chinese_compound(self):
        entities = extract_entities("上下文压缩器的工作方式")
        compound = [e for e in entities if e.type == ENTITY_COMPOUND]
        assert len(compound) > 0

    def test_english_nouns(self):
        entities = extract_entities("the agent uses memory system")
        nouns = [e for e in entities if e.type == ENTITY_NOUN]
        names = {e.name for e in nouns}
        assert "agent" in names
        assert "memory" in names

    def test_stop_words_filtered(self):
        """停用词不应被提取为实体。"""
        entities = extract_entities("这个那个应该可以但是")
        # 应该只有很少的实体（纯停用词组合）
        for e in entities:
            assert e.name not in {"这个", "那个", "应该", "可以", "但是"}

    def test_empty_text(self):
        assert extract_entities("") == []

    def test_deduplication(self):
        """同一实体同一类型只出现一次。"""
        entities = extract_entities("DeepSeek is DeepSeek")
        # 去重 key 是 (name.lower(), type)，同类型去重
        proper_count = sum(1 for e in entities
                          if e.name == "DeepSeek" and e.type == ENTITY_PROPER)
        assert proper_count == 1

    def test_sorted_by_confidence(self):
        entities = extract_entities('测试 "DeepSeek" 和 agent')
        for i in range(len(entities) - 1):
            assert entities[i].confidence >= entities[i + 1].confidence

    def test_english_identifier_snake_case(self):
        entities = extract_entities("use llm_client and tool_executor")
        compound = [e for e in entities if e.type == ENTITY_COMPOUND]
        names = {e.name for e in compound}
        assert "llm_client" in names or "tool_executor" in names


class TestBuildEntityIndex:
    def test_basic_index(self):
        docs = [
            {"id": "1", "content": "DeepSeek is an AI model", "metadata": {}},
            {"id": "2", "content": "Python programming language", "metadata": {}},
        ]
        index = build_entity_index(docs)
        # 应包含提取到的实体
        assert isinstance(index, dict)

    def test_metadata_entities(self):
        docs = [
            {"id": "1", "content": "simple content",
             "metadata": {"entities": ["DeepSeek", "Agent"]}},
        ]
        index = build_entity_index(docs)
        assert "DeepSeek" in index
        assert "Agent" in index

    def test_empty_documents(self):
        assert build_entity_index([]) == {}
