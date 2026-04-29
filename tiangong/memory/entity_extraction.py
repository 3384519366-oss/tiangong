"""实体提取 — 中文+英文实体识别。[借鉴mem0]

借鉴 mem0/memory/utils/entity_extraction.py:
- 4 类实体: PROPER(专有名词), QUOTED(引号内容), COMPOUND(复合名词), NOUN(关键名词)
- 无外部依赖（不依赖 spaCy），纯正则 + 模式匹配
- 用于检索时的实体加权 boost
"""

import re
from typing import List, Tuple


class Entity:
    """提取的实体。"""

    __slots__ = ("name", "type", "confidence")

    def __init__(self, name: str, entity_type: str, confidence: float = 0.8):
        self.name = name
        self.type = entity_type
        self.confidence = confidence

    def __repr__(self):
        return f"Entity({self.name!r}, {self.type}, {self.confidence:.2f})"


# ── 实体类型 ──
ENTITY_PROPER = "PROPER"       # 专有名词: 人名、地名、产品名、API名
ENTITY_QUOTED = "QUOTED"       # 引号内容: "天工"、'config.yaml'
ENTITY_COMPOUND = "COMPOUND"   # 复合名词: 上下文压缩器、deepseek-chat
ENTITY_NOUN = "NOUN"           # 关键名词: 记忆、Agent、token


# ── 提取模式 ──

# 引号内容
_QUOTED_PATTERN = re.compile(r"""['\"‘’“”]([^'\"]{1,30})['\"‘’“”]""")

# 中文专有名词: 大写开头 + 中文组成
_CN_PROPER_PATTERN = re.compile(
    r'(?:DeepSeek|OpenAI|Claude|Hermes|ChromaDB|SQLite|Python|JavaScript|'
    r'TypeScript|React|Docker|GitHub|Linux|macOS|Windows|API|SDK|UI|CLI|JSON|'
    r'YAML|XML|HTTP|REST|GPT|LLM|RAG|MCP|CDP)'
)

# 复合名词: 中文 2-6 字组合（非标点、非纯数字）
_CN_COMPOUND_PATTERN = re.compile(
    r'(?:[一-鿿]{2,6}(?:[·\-][一-鿿]{2,6})?)'
)

# 英文/驼峰/蛇形 标识符
_EN_IDENTIFIER_PATTERN = re.compile(
    r'\b(?:[a-z]+(?:[_-][a-z]+)+|[A-Z][a-z]+(?:[A-Z][a-z]+)+|'
    r'[a-z]+(?:[A-Z][a-z]+)+)\b'
)

# 英文关键名词（长度 >= 3）
_EN_NOUN_PATTERN = re.compile(r'\b([a-zA-Z]{3,})\b')

# URL/路径
_PATH_PATTERN = re.compile(r'(?:[\w-]+\.(?:py|md|yaml|yml|json|toml|js|ts|jsx|tsx|sh))')

# 停用词（中文）
_CN_STOP_WORDS = frozenset({
    '这个', '那个', '一个', '一些', '什么', '怎么', '为什么', '可以',
    '应该', '需要', '已经', '还是', '不过', '但是', '因为', '所以',
    '如果', '虽然', '而且', '然后', '之后', '之前', '现在', '以后',
    '使用', '进行', '没有', '不是', '不会', '不能', '可能', '已经',
    '我们', '你们', '他们', '自己', '大家', '这里', '那里', '第一',
    '第二', '第三', '第四', '第五', '你好', '谢谢', '比如', '例如',
    '包括', '关于', '对于', '或者', '以及', '为了', '作为', '按照',
    '不同', '知道', '觉得', '认为', '希望', '必须', '一定',
})

# 停用词（英文）
_EN_STOP_WORDS = frozenset({
    'the', 'and', 'for', 'that', 'this', 'with', 'from', 'have',
    'are', 'was', 'were', 'been', 'has', 'had', 'not', 'but',
    'its', 'also', 'can', 'all', 'any', 'each', 'they', 'their',
    'them', 'some', 'more', 'most', 'when', 'where', 'which',
    'what', 'who', 'how', 'will', 'just', 'only', 'other',
    'into', 'than', 'then', 'about', 'over', 'after', 'before',
    'between', 'should', 'would', 'could', 'there', 'here',
    'very', 'much', 'such', 'many', 'these', 'those', 'every',
    'because', 'while', 'during', 'without', 'within',
})


def extract_entities(text: str) -> List[Entity]:
    """从文本中提取实体。

    返回按置信度降序排列的实体列表。
    """
    entities: List[Entity] = []
    seen = set()

    def add(entity: Entity):
        key = (entity.name.lower(), entity.type)
        if key not in seen:
            seen.add(key)
            entities.append(entity)

    # 1. 引号内容 — 最高置信度
    for m in _QUOTED_PATTERN.finditer(text):
        name = m.group(1).strip()
        if 1 <= len(name) <= 30 and not name.isspace():
            add(Entity(name, ENTITY_QUOTED, confidence=0.95))

    # 2. 专有名词 — 高置信度
    for m in _CN_PROPER_PATTERN.finditer(text):
        add(Entity(m.group(), ENTITY_PROPER, confidence=0.90))

    # 3. 路径/文件名 — 高置信度
    for m in _PATH_PATTERN.finditer(text):
        add(Entity(m.group(), ENTITY_PROPER, confidence=0.85))

    # 4. 英文标识符（蛇形/驼峰）
    for m in _EN_IDENTIFIER_PATTERN.finditer(text):
        name = m.group()
        if name.lower() not in _EN_STOP_WORDS:
            add(Entity(name, ENTITY_COMPOUND, confidence=0.80))

    # 5. 英文名词（>= 3字符）
    for m in _EN_NOUN_PATTERN.finditer(text):
        name = m.group(1)
        if name.lower() not in _EN_STOP_WORDS:
            add(Entity(name, ENTITY_NOUN, confidence=0.60))

    # 6. 中文复合名词
    for m in _CN_COMPOUND_PATTERN.finditer(text):
        name = m.group()
        if name not in _CN_STOP_WORDS and len(name) >= 2:
            # 过滤纯标点/数字组合
            if not re.match(r'^[\d\W_]+$', name):
                add(Entity(name, ENTITY_COMPOUND, confidence=0.70))

    entities.sort(key=lambda e: e.confidence, reverse=True)
    return entities


def build_entity_index(documents: List[dict]) -> dict:
    """从文档列表构建实体→文档映射索引。

    documents: [{id, content, metadata, ...}]
    返回: {entity_name: [(doc_id, confidence), ...]}
    """
    index: dict = {}
    for doc in documents:
        doc_id = doc.get("id", "")
        content = doc.get("content", "") or ""
        entities = extract_entities(content)

        for e in entities:
            if e.name not in index:
                index[e.name] = []
            index[e.name].append((doc_id, e.confidence))

        # 也索引 metadata 中的 entities 字段
        meta_entities = doc.get("metadata", {}).get("entities", [])
        if isinstance(meta_entities, list):
            for ent_name in meta_entities:
                if isinstance(ent_name, str):
                    if ent_name not in index:
                        index[ent_name] = []
                    index[ent_name].append((doc_id, 0.9))

    return index
