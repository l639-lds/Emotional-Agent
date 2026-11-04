"""纯标准库的字符 n-gram TF-IDF 余弦相似度（不依赖分词、不依赖 numpy）。

为什么用字符 2-gram：
- 中文不需要分词，任何输入都能 tokenize，且对错别字/口语化写法更鲁棒；
- 纯 Python 实现可在 3 秒内跑完全部单测，离线可复现（SPEC §7.2 / §7.5）。

约定：
- :func:`normalize` 只保留中文字符、拉丁字母与数字，丢弃标点空白，因此「！」「？」不会污染特征；
- :func:`term_frequencies` 使用次线性词频 ``1 + log(count)`` 抑制长文本重复；
- 两个文档构成语料时的 idf 用平滑公式 ``log((N+1)/(df+1)) + 1``；
- 空串、单字符、纯标点、全空白都返回 0.0（**不除零**）。
"""

from __future__ import annotations

import math
import re
from typing import Iterable

# 只保留中文、拉丁字母、数字；标点与空白视为分隔符直接丢弃
_KEEP_RE = re.compile(r"[^\u4e00-\u9fff\u3040-\u30ffa-zA-Z0-9]+")
# 分句/分短语用：中文标点、空白、中点、冒号都算边界（用于取"最佳片段"余弦）
_SPLIT_RE = re.compile(r"[。；;，,!！?？:：、·•~～…\s\n\r\t]+")
# 虚词字表：含这些字的 n-gram 视为语气/结构片段，不算"实词"
STOP_CHARS = frozenset(
    "的了是我你他她它在有和就都也还很太点个吧呢啊呀吗嘛么什怎啥这那着过又再才只"
    "不没别会要想能可应该把被给让跟向从对为以所而且但或与及每各些多么之其此"
)


def normalize(text: str) -> str:
    """归一化文本：去标点空白、拉丁字母转小写。"""
    if not text:
        return ""
    return _KEEP_RE.sub("", str(text)).lower()


def content_ngrams(text: str, n: int = 2) -> set[str]:
    """只保留"实词"n-gram：含虚词字的片段被丢掉；全虚词时退化为全部 n-gram。"""
    grams = {gram for gram in char_ngrams(text, n) if gram.strip()}
    content = {gram for gram in grams if all(char not in STOP_CHARS for char in gram)}
    return content or grams


def char_ngrams(text: str, n: int = 2) -> list[str]:
    """把文本切成字符 n-gram 列表；文本太短时自动退化为单字符。"""
    normalized = normalize(text)
    if not normalized:
        return []
    if len(normalized) < n:
        return list(normalized)
    return [normalized[i : i + n] for i in range(len(normalized) - n + 1)]


def term_frequencies(text: str, n: int = 2) -> dict[str, float]:
    """返回 ``{n-gram: 次线性词频}``；空文本返回空 dict。"""
    counts: dict[str, int] = {}
    for gram in char_ngrams(text, n):
        counts[gram] = counts.get(gram, 0) + 1
    return {gram: 1.0 + math.log(count) for gram, count in counts.items()}


def _idf(documents: Iterable[dict[str, float]]) -> dict[str, float]:
    """在给定文档集合上算平滑 idf（N=文档数，df=包含该特征的文档数）。"""
    docs = list(documents)
    doc_count = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for gram in doc:
            df[gram] = df.get(gram, 0) + 1
    return {
        gram: math.log((doc_count + 1) / (count + 1)) + 1.0 for gram, count in df.items()
    }


def _weighted(terms: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    """词频 × idf，得到加权向量。"""
    return {gram: tf * idf.get(gram, 1.0) for gram, tf in terms.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    """两个稀疏向量的余弦相似度；任一为零向量时返回 0.0。"""
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(gram, 0.0) for gram, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def similarity(left: str, right: str, n: int = 2) -> float:
    """两段文本的字符 n-gram TF-IDF 余弦相似度，取值 0..1。

    Args:
        left: 文本 A（如用户这一句）。
        right: 文本 B（如记忆条目）。
        n: 字符 n-gram 的 n，默认 2。

    Returns:
        相似度；任一文本为空/纯标点时为 0.0。
    """
    if not normalize(left) or not normalize(right):
        return 0.0
    left_terms = term_frequencies(left, n)
    right_terms = term_frequencies(right, n)
    if not left_terms or not right_terms:
        return 0.0
    if normalize(left) == normalize(right):
        return 1.0
    # 两个文档构成语料；单字符退化时同样适用
    idf = _idf([left_terms, right_terms])
    return cosine(_weighted(left_terms, idf), _weighted(right_terms, idf))


def query_cover(query: str, text: str, n: int = 2) -> float:
    """查询里的「实词片段」有多少出现在条目里（0..1）。

    先按 :data:`STOP_CHARS` 去掉「的/了/还是/有点」这类虚词 n-gram，只留实词片段，
    这样 ``面试还是有点慌`` 只要命中 ``面试``，覆盖率就是 1.0，不会被语气词稀释。
    短查询对长文档做余弦时会被长度惩罚压到接近 0，这一项正好补足"关键词说到没说到"。
    查询为空、纯标点或只有虚词时返回 0.0。
    """
    query_grams = content_ngrams(query, n)
    if not query_grams:
        return 0.0
    text_grams = {gram for gram in char_ngrams(text, n) if gram.strip()}
    if not text_grams:
        return 0.0
    return len(query_grams & text_grams) / len(query_grams)


def best_cosine(query: str, text: str, n: int = 2) -> float:
    """查询与条目"最佳片段"的余弦：对分句/分短语后取最大值。

    条目常由「标题 + 摘要 + 话题词」拼成，整段的长度惩罚会让相关句子被稀释；
    取最佳片段更接近人判断"这条记忆跟我这句有关"的方式。
    """
    whole = similarity(query, text, n)
    if whole >= 0.999:
        return whole
    best = whole
    for part in _SPLIT_RE.split(str(text or ""))[:24]:
        if len(part.strip()) < n:
            continue
        best = max(best, similarity(query, part, n))
    return best


def relevance(query: str, text: str, n: int = 2) -> float:
    """检索用相似度（0..1）：``0.6 * 最佳片段余弦 + 0.4 * 实词覆盖率``。

    与纯余弦的 :func:`similarity` 区别在于：短查询不会被长条目的长度惩罚压没，
    因此可以直接和 ``MOOD_GATE_SIM`` 这类阈值比较。合并/冲突判定仍用
    :func:`similarity`（纯余弦），避免 SPEC 里 0.82/0.5 这些阈值的语义被改变。
    """
    if not normalize(query) or not normalize(text):
        return 0.0
    return max(0.0, min(1.0, 0.6 * best_cosine(query, text, n) + 0.4 * query_cover(query, text, n)))


def common_terms(left: str, right: str, *, limit: int = 3, n: int = 2) -> list[str]:
    """返回两段文本共有的 n-gram（长一点的优先），用于生成中文解释理由。"""
    left_grams = set(char_ngrams(left, n))
    right_grams = set(char_ngrams(right, n))
    shared = [gram for gram in left_grams & right_grams if gram]
    shared.sort(key=lambda gram: (-len(gram), gram))
    return shared[:limit]


def top_terms(text: str, limit: int = 6, n: int = 2) -> list[str]:
    """按词频挑出文本里最具代表性的 n-gram（无 idf，仅用于话题词兜底）。"""
    terms = term_frequencies(text, n)
    ranked = sorted(terms.items(), key=lambda pair: (-pair[1], pair[0]))
    return [gram for gram, _ in ranked[:limit]]


def keyword_hits(query: str, keywords: Iterable[str]) -> list[str]:
    """统计 query 命中了哪些关键词（子串包含即可，用于解释「为什么召回」）。"""
    normalized_query = normalize(query)
    if not normalized_query:
        return []
    hits: list[str] = []
    for keyword in keywords:
        normalized_keyword = normalize(keyword)
        if normalized_keyword and normalized_keyword in normalized_query:
            hits.append(str(keyword))
    return hits[:3]


__all__ = [
    "char_ngrams",
    "common_terms",
    "cosine",
    "keyword_hits",
    "normalize",
    "query_cover",
    "relevance",
    "similarity",
    "term_frequencies",
    "top_terms",
]
