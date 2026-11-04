"""五层记忆的数据类定义与 JSON 互转（v4 schema）。

约定：
- 所有时间戳统一为**毫秒浮点**（与 SPEC §2 的「毫秒时间戳」一致）。
  为了让 `app.py` 直接传 `time.time()` 也能工作，:func:`as_ms` 会把小于
  ``1e11`` 的入参视为「秒」并自动乘 1000；业务代码只需要调用它。
- 每个数据类都提供 ``to_dict()`` / ``from_dict()``，解析时**逐字段降级**：
  缺字段或类型不对时用默认值兜底，绝不抛异常（记忆文件是长期资产，不能因脏数据崩溃）。
- ``MemoryBundle`` / ``MemoryTrace`` / ``TraceItem`` / ``ConsolidationResult`` 也在此定义，
  供 retrieval / consolidate / engine 共用，避免循环 import。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.config import CATEGORIES, MEMORY_VERSION, clamp, stage_of

# 未知分类统一归到「近况」（SPEC §2）
FALLBACK_CATEGORY = "近况"

# 小于该值的时间戳按「秒」理解（约公元 5138 年的毫秒值），用于兼容 time.time()
_SECONDS_CUTOFF = 1e11

TIMESTAMP_LABEL_KEYS = ("ts_label", "created_label", "last_confirmed_label")


# ---------------------------------------------------------------------------
# 时间与 id 工具
# ---------------------------------------------------------------------------


def now_ms() -> float:
    """返回当前时间的毫秒时间戳（float）。"""
    return time.time() * 1000.0


def as_ms(value: float | int | None) -> float:
    """把入参时间戳归一为毫秒；None / 非法值 / 非正数回落到当前时间。"""
    if value is None:
        return now_ms()
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return now_ms()
    if stamp <= 0:
        return now_ms()
    # 秒级时间戳（如 time.time()）自动升级为毫秒，避免 Δt 差三个数量级
    return stamp * 1000.0 if stamp < _SECONDS_CUTOFF else stamp


def new_id(prefix: str) -> str:
    """生成稳定格式的短 id：``f"{prefix}_{uuid4().hex[:10]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def days_between(later_ms: float, earlier_ms: float) -> float:
    """两个毫秒时间戳相差多少天（可能为负）。"""
    return (float(later_ms) - float(earlier_ms)) / 86_400_000.0


# ---------------------------------------------------------------------------
# 逐字段降级的小工具
# ---------------------------------------------------------------------------


def _as_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _as_str(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (int, float)):
        return str(raw)
    return default


def _as_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(raw, (int, float)):
        return bool(raw)
    return default


def _as_str_list(raw: Any, *, limit: int = 24) -> list[str]:
    """把任意输入整理成去重后的字符串列表（保持顺序）。"""
    if isinstance(raw, str):
        candidates: list[Any] = [part for part in raw.replace("、", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        candidates = []
    out: list[str] = []
    for item in candidates:
        text = _as_str(item)
        if text and text not in out:
            out.append(text[:40])
        if len(out) >= limit:
            break
    return out


def normalize_category(raw: Any) -> str:
    """把分类归一为 :data:`core.config.CATEGORIES` 之一，未知归「近况」。"""
    text = _as_str(raw)
    return text if text in CATEGORIES else FALLBACK_CATEGORY


def normalize_scope(raw: Any, category: str, shared_categories: frozenset[str]) -> str:
    """归一记忆作用域；只有客观类事实才允许 ``shared``（SPEC §3.2 第 4 步）。"""
    scope = _as_str(raw, "private").lower()
    if scope == "shared" and category in shared_categories:
        return "shared"
    return "private"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class WorkingTurn:
    """短期记忆中的一条对话轮次。"""

    role: str = "user"
    content: str = ""
    ts: float = 0.0
    emotion: str | None = None
    salience: float = 0.5

    def __post_init__(self) -> None:
        self.role = "assistant" if _as_str(self.role) == "assistant" else "user"
        self.content = _as_str(self.content)
        self.ts = as_ms(self.ts) if self.ts else now_ms()
        self.emotion = _as_str(self.emotion) or None
        self.salience = clamp(_as_float(self.salience, 0.5), 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "emotion": self.emotion,
            "salience": self.salience,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "WorkingTurn":
        """从 dict 还原；脏数据逐字段降级。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            ts=data.get("ts") or now_ms(),
            emotion=data.get("emotion"),
            salience=data.get("salience", 0.5),
        )


@dataclass
class Episode:
    """中期记忆：一段对话被压缩成的情景记忆。"""

    id: str = ""
    title: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    valence: float = 0.0
    arousal: float = 0.5
    importance: float = 0.5
    turn_start: int = 0
    turn_end: int = 0
    created_at: float = 0.0
    last_recalled_at: float = 0.0
    recall_count: int = 0
    base_strength: float = 1.0
    archived: bool = False

    def __post_init__(self) -> None:
        self.id = _as_str(self.id) or new_id("ep")
        self.title = _as_str(self.title)[:60] or "一段对话"
        self.summary = _as_str(self.summary)[:280]
        self.topics = _as_str_list(self.topics, limit=8)
        self.keywords = _as_str_list(self.keywords or self.topics, limit=12)
        self.valence = clamp(_as_float(self.valence, 0.0), -1.0, 1.0)
        self.arousal = clamp(_as_float(self.arousal, 0.5), 0.0, 1.0)
        self.importance = clamp(_as_float(self.importance, 0.5), 0.0, 1.0)
        self.created_at = as_ms(self.created_at) if self.created_at else now_ms()
        self.last_recalled_at = (
            as_ms(self.last_recalled_at) if self.last_recalled_at else self.created_at
        )
        self.recall_count = max(0, _as_int(self.recall_count, 0))
        self.base_strength = clamp(_as_float(self.base_strength, 1.0), 0.0, 1.0)
        self.archived = _as_bool(self.archived)

    @property
    def search_text(self) -> str:
        """用于计算相似度的文本（标题 + 摘要 + 话题 + 检索词）。"""
        parts = [self.title, self.summary, "、".join(self.topics), "、".join(self.keywords)]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "topics": list(self.topics),
            "keywords": list(self.keywords),
            "valence": self.valence,
            "arousal": self.arousal,
            "importance": self.importance,
            "turn_start": self.turn_start,
            "turn_end": self.turn_end,
            "created_at": self.created_at,
            "last_recalled_at": self.last_recalled_at,
            "recall_count": self.recall_count,
            "base_strength": self.base_strength,
            "archived": self.archived,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Episode":
        """从 dict 还原；缺字段用默认值。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            topics=data.get("topics", []),
            keywords=data.get("keywords", []),
            valence=data.get("valence", 0.0),
            arousal=data.get("arousal", 0.5),
            importance=data.get("importance", 0.5),
            turn_start=_as_int(data.get("turn_start"), 0),
            turn_end=_as_int(data.get("turn_end"), 0),
            created_at=data.get("created_at", 0.0),
            last_recalled_at=data.get("last_recalled_at", 0.0),
            recall_count=data.get("recall_count", 0),
            base_strength=data.get("base_strength", 1.0),
            archived=data.get("archived", False),
        )


@dataclass
class Fact:
    """长期记忆：关于用户的一句话事实（第三人称，含「用户」）。"""

    id: str = ""
    content: str = ""
    category: str = FALLBACK_CATEGORY
    confidence: float = 0.6
    evidence_count: int = 1
    first_seen: float = 0.0
    last_confirmed: float = 0.0
    scope: str = "private"
    superseded_by: str | None = None
    recall_count: int = 0
    base_strength: float = 1.0
    archived: bool = False
    origin: str = "working"

    def __post_init__(self) -> None:
        self.id = _as_str(self.id) or new_id("fact")
        self.content = _as_str(self.content)[:160]
        self.category = normalize_category(self.category)
        self.confidence = clamp(_as_float(self.confidence, 0.6), 0.0, 1.0)
        self.evidence_count = max(1, _as_int(self.evidence_count, 1))
        self.first_seen = as_ms(self.first_seen) if self.first_seen else now_ms()
        self.last_confirmed = (
            as_ms(self.last_confirmed) if self.last_confirmed else self.first_seen
        )
        self.scope = "shared" if _as_str(self.scope).lower() == "shared" else "private"
        self.superseded_by = _as_str(self.superseded_by) or None
        self.recall_count = max(0, _as_int(self.recall_count, 0))
        self.base_strength = clamp(_as_float(self.base_strength, 1.0), 0.0, 1.0)
        self.archived = _as_bool(self.archived)
        self.origin = _as_str(self.origin, "working") or "working"

    @property
    def active(self) -> bool:
        """是否仍在生效（未归档且未被取代）。"""
        return not self.archived and not self.superseded_by

    @property
    def search_text(self) -> str:
        """用于计算相似度的文本（事实内容 + 分类）。"""
        return f"{self.content} {self.category}"

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "first_seen": self.first_seen,
            "last_confirmed": self.last_confirmed,
            "scope": self.scope,
            "superseded_by": self.superseded_by,
            "recall_count": self.recall_count,
            "base_strength": self.base_strength,
            "archived": self.archived,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Fact":
        """从 dict 还原；缺字段用默认值。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            category=data.get("category", FALLBACK_CATEGORY),
            confidence=data.get("confidence", 0.6),
            evidence_count=data.get("evidence_count", 1),
            first_seen=data.get("first_seen", 0.0),
            last_confirmed=data.get("last_confirmed", 0.0),
            scope=data.get("scope", "private"),
            superseded_by=data.get("superseded_by"),
            recall_count=data.get("recall_count", 0),
            base_strength=data.get("base_strength", 1.0),
            archived=data.get("archived", False),
            origin=data.get("origin", "working"),
        )


@dataclass
class OpenLoop:
    """未闭环事件：待办 / 承诺 / 悬念 / 情绪牵挂。"""

    id: str = ""
    what: str = ""
    kind: str = "待办"
    due_hint: str | None = None
    status: str = "open"
    created_at: float = 0.0
    closed_at: float | None = None
    mention_count: int = 1

    def __post_init__(self) -> None:
        self.id = _as_str(self.id) or new_id("loop")
        self.what = _as_str(self.what)[:80]
        self.kind = _as_str(self.kind, "待办") or "待办"
        self.due_hint = _as_str(self.due_hint) or None
        self.status = "closed" if _as_str(self.status).lower() == "closed" else "open"
        self.created_at = as_ms(self.created_at) if self.created_at else now_ms()
        self.closed_at = as_ms(self.closed_at) if self.closed_at else None
        self.mention_count = max(1, _as_int(self.mention_count, 1))

    @property
    def open(self) -> bool:
        """是否仍未闭环。"""
        return self.status == "open"

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "id": self.id,
            "what": self.what,
            "kind": self.kind,
            "due_hint": self.due_hint,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "mention_count": self.mention_count,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "OpenLoop":
        """从 dict 还原；缺字段用默认值。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=data.get("id", ""),
            what=data.get("what", ""),
            kind=data.get("kind", "待办"),
            due_hint=data.get("due_hint"),
            status=data.get("status", "open"),
            created_at=data.get("created_at", 0.0),
            closed_at=data.get("closed_at"),
            mention_count=data.get("mention_count", 1),
        )


@dataclass
class RelationshipState:
    """关系档案：驱动力人格漂移（创新点 5）。"""

    intimacy: float = 0.0
    trust: float = 0.0
    stage: str = "陌生"
    nickname: str | None = None
    nickname_history: list[dict] = field(default_factory=list)
    inside_jokes: list[str] = field(default_factory=list)
    shared_themes: list[str] = field(default_factory=list)
    turn_count: int = 0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        self.intimacy = clamp(_as_float(self.intimacy, 0.0), 0.0, 100.0)
        self.trust = clamp(_as_float(self.trust, 0.0), 0.0, 100.0)
        self.stage = stage_of(self.intimacy)
        self.nickname = _as_str(self.nickname) or None
        self.nickname_history = [
            {"value": _as_str(item.get("value")), "at": as_ms(item.get("at") or 0)}
            for item in (self.nickname_history or [])
            if isinstance(item, dict) and _as_str(item.get("value"))
        ]
        self.inside_jokes = _as_str_list(self.inside_jokes, limit=5)
        self.shared_themes = _as_str_list(self.shared_themes, limit=8)
        self.turn_count = max(0, _as_int(self.turn_count, 0))
        self.updated_at = as_ms(self.updated_at) if self.updated_at else now_ms()

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "intimacy": self.intimacy,
            "trust": self.trust,
            "stage": self.stage,
            "nickname": self.nickname,
            "nickname_history": [dict(item) for item in self.nickname_history],
            "inside_jokes": list(self.inside_jokes),
            "shared_themes": list(self.shared_themes),
            "turn_count": self.turn_count,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RelationshipState":
        """从 dict 还原；缺字段用默认值。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            intimacy=data.get("intimacy", 0.0),
            trust=data.get("trust", 0.0),
            stage=data.get("stage", "陌生"),
            nickname=data.get("nickname"),
            nickname_history=data.get("nickname_history", []),
            inside_jokes=data.get("inside_jokes", []),
            shared_themes=data.get("shared_themes", []),
            turn_count=data.get("turn_count", 0),
            updated_at=data.get("updated_at", 0.0),
        )


@dataclass
class TraceItem:
    """可解释记忆的单条（UI 的 Trace 面板直接渲染）。"""

    tier: str = "long"
    kind: str = "fact"
    id: str = ""
    text: str = ""
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "tier": self.tier,
            "kind": self.kind,
            "id": self.id,
            "text": self.text,
            "score": round(self.score, 4),
            "components": {key: round(value, 4) for key, value in self.components.items()},
            "reason": self.reason,
            "pinned": self.pinned,
        }


@dataclass
class MemoryTrace:
    """一轮检索的可解释记录。"""

    items: list[TraceItem] = field(default_factory=list)
    skipped: list[TraceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    budget: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "items": [item.to_dict() for item in self.items],
            "skipped": [item.to_dict() for item in self.skipped],
            "notes": list(self.notes),
            "budget": self.budget,
        }


@dataclass
class MemoryBundle:
    """一次检索的结果（读路径的唯一出口）。"""

    role: str = ""
    relationship: dict[str, Any] = field(default_factory=dict)
    facts: list[TraceItem] = field(default_factory=list)
    episodes: list[TraceItem] = field(default_factory=list)
    loops: list[OpenLoop] = field(default_factory=list)
    working_turns: list[WorkingTurn] = field(default_factory=list)
    working_summary: str = ""
    trace: MemoryTrace = field(default_factory=MemoryTrace)
    style_directives: list[str] = field(default_factory=list)
    block: str = ""
    include_self: bool = False
    self_notes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict（调试/落盘用）。"""
        return {
            "role": self.role,
            "relationship": dict(self.relationship),
            "facts": [item.to_dict() for item in self.facts],
            "episodes": [item.to_dict() for item in self.episodes],
            "loops": [loop.to_dict() for loop in self.loops],
            "working_turns": [turn.to_dict() for turn in self.working_turns],
            "working_summary": self.working_summary,
            "trace": self.trace.to_dict(),
            "style_directives": list(self.style_directives),
            "block": self.block,
            "include_self": self.include_self,
            "self_notes": [dict(item) for item in self.self_notes],
        }


@dataclass
class ConsolidationResult:
    """一次「睡眠整理」的结果（UI toast / Trace 面板使用）。"""

    done: bool = False
    episode_id: str | None = None
    new_facts: list[str] = field(default_factory=list)
    updated_facts: list[str] = field(default_factory=list)
    superseded: list[dict] = field(default_factory=list)
    loops_opened: list[str] = field(default_factory=list)
    loops_closed: list[str] = field(default_factory=list)
    relationship: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    used_llm: bool = True

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "done": self.done,
            "episode_id": self.episode_id,
            "new_facts": list(self.new_facts),
            "updated_facts": list(self.updated_facts),
            "superseded": [dict(item) for item in self.superseded],
            "loops_opened": list(self.loops_opened),
            "loops_closed": list(self.loops_closed),
            "relationship": dict(self.relationship),
            "notes": list(self.notes),
            "used_llm": self.used_llm,
        }


@dataclass
class SelfNote:
    """L4 自我认知：Agent 对自己的一段叙述（``content`` 一句话，≤120 字）。"""

    id: str = ""
    content: str = ""
    note_type: str = "成长"
    created_at: float = 0.0
    updated_at: float = 0.0
    mention_count: int = 1

    def __post_init__(self) -> None:
        self.id = _as_str(self.id) or new_id("self")
        self.content = _as_str(self.content)[:120]
        self.note_type = _as_str(self.note_type, "成长") or "成长"
        self.created_at = as_ms(self.created_at) if self.created_at else now_ms()
        self.updated_at = as_ms(self.updated_at) if self.updated_at else self.created_at
        self.mention_count = max(1, _as_int(self.mention_count, 1))

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "id": self.id,
            "content": self.content,
            "note_type": self.note_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "mention_count": self.mention_count,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SelfNote":
        """从 dict 还原；缺字段用默认值，脏数据不抛异常。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            note_type=data.get("note_type", "成长"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            mention_count=data.get("mention_count", 1),
        )


@dataclass
class MemorySnapshot:
    """L3 固化快照的索引项（快照正文另存为 ``data/archive/<角色>/<id>.json``）。"""

    id: str = ""
    created_at: float = 0.0
    label: str = ""
    episode_id: str = ""
    episode_title: str = ""
    facts_added: int = 0
    facts_updated: int = 0
    self_note: str = ""
    path: str = ""

    def __post_init__(self) -> None:
        self.id = _as_str(self.id) or new_id("snap")
        self.created_at = as_ms(self.created_at) if self.created_at else now_ms()
        self.label = _as_str(self.label, "") or ""
        self.episode_id = _as_str(self.episode_id)
        self.episode_title = _as_str(self.episode_title)[:60]
        self.facts_added = max(0, _as_int(self.facts_added, 0))
        self.facts_updated = max(0, _as_int(self.facts_updated, 0))
        self.self_note = _as_str(self.self_note)[:120]
        self.path = _as_str(self.path, "")

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict。"""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "label": self.label,
            "episode_id": self.episode_id,
            "episode_title": self.episode_title,
            "facts_added": self.facts_added,
            "facts_updated": self.facts_updated,
            "self_note": self.self_note,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "MemorySnapshot":
        """从 dict 还原；缺字段用默认值，脏数据不抛异常。"""
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=data.get("id", ""),
            created_at=data.get("created_at", 0.0),
            label=data.get("label", ""),
            episode_id=data.get("episode_id", ""),
            episode_title=data.get("episode_title", ""),
            facts_added=data.get("facts_added", 0),
            facts_updated=data.get("facts_updated", 0),
            self_note=data.get("self_note", ""),
            path=data.get("path", ""),
        )


@dataclass
class RoleMemory:
    """单个角色的三层记忆（``roles[角色名]`` 的运行时形态）。"""

    turns: list[WorkingTurn] = field(default_factory=list)
    working_summary: str = ""
    summary_until_turn: int = 0
    episodes: list[Episode] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    open_loops: list[OpenLoop] = field(default_factory=list)
    relationship: RelationshipState = field(default_factory=RelationshipState)
    last_consolidated_turn: int = 0
    turn_counter: int = 0
    self_notes: list[SelfNote] = field(default_factory=list)
    snapshots: list[MemorySnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的 dict（即 SPEC §2 的 RoleMemory 结构）。"""
        return {
            "turns": [turn.to_dict() for turn in self.turns],
            "working_summary": self.working_summary,
            "summary_until_turn": self.summary_until_turn,
            "episodes": [episode.to_dict() for episode in self.episodes],
            "facts": [fact.to_dict() for fact in self.facts],
            "open_loops": [loop.to_dict() for loop in self.open_loops],
            "relationship": self.relationship.to_dict(),
            "last_consolidated_turn": self.last_consolidated_turn,
            "turn_counter": self.turn_counter,
            "self_notes": [note.to_dict() for note in self.self_notes],
            "snapshots": [snap.to_dict() for snap in self.snapshots],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RoleMemory":
        """从 dict 还原；缺字段用默认值，脏数据不抛异常（v3 旧文件缺两层 → 空列表）。"""
        data = raw if isinstance(raw, dict) else {}
        turns_raw = data.get("turns") if isinstance(data.get("turns"), list) else []
        episodes_raw = data.get("episodes") if isinstance(data.get("episodes"), list) else []
        facts_raw = data.get("facts") if isinstance(data.get("facts"), list) else []
        loops_raw = data.get("open_loops") if isinstance(data.get("open_loops"), list) else []
        self_raw = data.get("self_notes") if isinstance(data.get("self_notes"), list) else []
        snaps_raw = data.get("snapshots") if isinstance(data.get("snapshots"), list) else []
        return cls(
            turns=[WorkingTurn.from_dict(item) for item in turns_raw if isinstance(item, dict)],
            working_summary=_as_str(data.get("working_summary"))[:600],
            summary_until_turn=max(0, _as_int(data.get("summary_until_turn"), 0)),
            episodes=[Episode.from_dict(item) for item in episodes_raw if isinstance(item, dict)],
            facts=[Fact.from_dict(item) for item in facts_raw if isinstance(item, dict)],
            open_loops=[OpenLoop.from_dict(item) for item in loops_raw if isinstance(item, dict)],
            relationship=RelationshipState.from_dict(data.get("relationship")),
            last_consolidated_turn=max(0, _as_int(data.get("last_consolidated_turn"), 0)),
            turn_counter=max(0, _as_int(data.get("turn_counter"), 0)),
            self_notes=[SelfNote.from_dict(item) for item in self_raw if isinstance(item, dict)],
            snapshots=[
                MemorySnapshot.from_dict(item) for item in snaps_raw if isinstance(item, dict)
            ],
        )


# ---------------------------------------------------------------------------
# data（整个 memory.json）级别的读写助手
# ---------------------------------------------------------------------------


def default_data() -> dict[str, Any]:
    """v4 存储的默认结构（五层记忆；仍兼容读入 v2/v3 旧文件）。"""
    return {"version": MEMORY_VERSION, "chats": {}, "roles": {}, "shared_facts": [], "meta": {}}


def get_role_memory(data: dict[str, Any], role: str) -> RoleMemory:
    """读取某角色的 RoleMemory（不存在则返回默认值，不写回）。"""
    roles = data.get("roles")
    raw = roles.get(role) if isinstance(roles, dict) else None
    memory = RoleMemory.from_dict(raw)
    # roles 里的 dict 不保证字段齐全，这里顺便补齐结构，避免后续 KeyError
    if isinstance(roles, dict) and not isinstance(raw, dict):
        roles[role] = memory.to_dict()
    return memory


def set_role_memory(data: dict[str, Any], role: str, memory: RoleMemory) -> None:
    """把 RoleMemory 写回 data（保持 JSON 原生结构）。"""
    roles = data.setdefault("roles", {})
    roles[role] = memory.to_dict()


def get_shared_facts(data: dict[str, Any]) -> list[Fact]:
    """读取跨角色共享的客观事实列表。"""
    raw = data.get("shared_facts")
    if not isinstance(raw, list):
        data["shared_facts"] = []
        return []
    return [Fact.from_dict(item) for item in raw if isinstance(item, dict)]


def set_shared_facts(data: dict[str, Any], facts: list[Fact]) -> None:
    """写回跨角色共享事实列表。"""
    data["shared_facts"] = [fact.to_dict() for fact in facts]


__all__ = [
    "ConsolidationResult",
    "Episode",
    "Fact",
    "MemoryBundle",
    "MemorySnapshot",
    "MemoryTrace",
    "OpenLoop",
    "RelationshipState",
    "RoleMemory",
    "SelfNote",
    "TIMESTAMP_LABEL_KEYS",
    "TraceItem",
    "WorkingTurn",
    "as_ms",
    "days_between",
    "default_data",
    "get_role_memory",
    "get_shared_facts",
    "new_id",
    "normalize_category",
    "normalize_scope",
    "now_ms",
    "set_role_memory",
    "set_shared_facts",
]
