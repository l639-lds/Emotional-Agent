"""persona_forge 数据模型：人设卡、自评、体检报告、编译结果。

本模块只做两件事：

1. 用 dataclass 固化「人设卡」的字段契约（见 `docs/SPEC.md` §5），供 UI 与渲染层直接读取；
2. 提供**极度宽容**的 dict ↔ dataclass 互转：输入可能来自 LLM 脏输出、用户历史数据、
   演示模式假数据，缺字段 / 类型错 / 列表里混入 None 都必须被清洗成合法值，**绝不抛异常**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.config import CANONICAL_STAGES

# 自评的 6 个固定维度（键名不允许改动，UI 直接按此渲染雷达图 / 条形图）
CRITIQUE_DIMENSIONS: tuple[str, ...] = ("可执行性", "一致性", "差异化", "情感深度", "安全性", "可演示性")

# identity 字段的固定子键
IDENTITY_KEYS: tuple[str, ...] = ("age_vibe", "occupation", "setting")

# 台词锚点必须覆盖的三种场景（元 Prompt 里会对模型提硬性要求）
ANCHOR_SCENES: tuple[str, ...] = ("高兴时", "被冒犯时", "安慰人时")

# 「无内容」的各种写法，统一识别为 None
_EMPTY_TOKENS = frozenset({"", "null", "none", "n/a", "无", "没有", "未指定", "未知"})


# ---------------------------------------------------------------------------
# 清洗工具（全部容错、永不抛异常）
# ---------------------------------------------------------------------------


def as_text(raw: Any, *, default: str = "", limit: int = 600) -> str:
    """把任意值清洗成字符串；非法值回落到 default，超长按 limit 截断。"""
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, (int, float)):
        text = str(int(raw)) if float(raw).is_integer() else str(raw)
    elif isinstance(raw, (list, tuple)):
        text = "、".join(part for part in (as_text(item, limit=80) for item in raw) if part)
    elif isinstance(raw, dict):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        return default
    if text.strip().lower() in _EMPTY_TOKENS:
        return default
    return text.strip()[:limit]


def as_text_list(
    raw: Any,
    *,
    limit: int = 8,
    item_limit: int = 80,
    min_items: int = 0,
    default: Iterable[str] = (),
) -> list[str]:
    """把任意值清洗成字符串列表：去掉 None/空串/重复项，元素超长截断，条数受 limit 限制。"""
    items: list[str] = []
    if isinstance(raw, str):
        # 允许模型用「；」「|」「/」换行等把列表塞进一个字符串
        raw = [part for part in raw.replace("\n", "；").replace("|", "；").split("；")]
    if isinstance(raw, (list, tuple)):
        for item in raw:
            text = as_text(item, limit=item_limit)
            if text and text not in items:
                items.append(text)
    elif isinstance(raw, dict):
        for value in raw.values():
            text = as_text(value, limit=item_limit)
            if text and text not in items:
                items.append(text)
    else:
        text = as_text(raw, limit=item_limit)
        if text:
            items.append(text)

    if len(items) < min_items:
        for fallback in default:
            text = as_text(fallback, limit=item_limit)
            if text and text not in items:
                items.append(text)
            if len(items) >= min_items:
                break
    return items[:limit] if limit > 0 else items


def as_int(raw: Any, default: int, *, low: int = 0, high: int = 100) -> int:
    """把任意值清洗成区间内的整数（"22" / "22.7" / True 都能吃）。"""
    if isinstance(raw, bool):
        return default
    try:
        if isinstance(raw, str):
            raw = raw.strip().rstrip("%分").strip()
            if raw.lower() in _EMPTY_TOKENS:
                return default
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def as_float(raw: Any, default: float, *, low: float = 0.0, high: float = 1.0) -> float:
    """把任意值清洗成区间内的浮点数。"""
    if isinstance(raw, bool):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SpeechProfile:
    """语音指纹：让模型"听起来就是他"的可执行参数（不是形容词）。"""

    register: str = ""                      # 语域：如"短句 + 审讯式追问"
    avg_sentence_len: int = 20              # 平均句长（字），用于控制话痨/克制
    tics: list[str] = field(default_factory=list)          # 口头禅 / 习惯插入语
    punctuation: str = ""                   # 标点习惯：如"句号收尾，几乎不用感叹号"
    banned_words: list[str] = field(default_factory=list)  # 本角色绝不说出口的词
    sample_lines: list[str] = field(default_factory=list)  # 台词锚点（目标 3 条）


@dataclass
class EmotionRule:
    """情绪反应表的一条：什么触发 → 说什么 → 身体怎么做。"""

    trigger: str = ""
    reaction: str = ""
    body_language: str = ""


@dataclass
class PersonaCard:
    """结构化人设卡：一句描述被编译后的全部可执行指令。"""

    name: str = ""
    tagline: str = ""
    avatar: str = ""
    identity: dict[str, str] = field(default_factory=lambda: {key: "" for key in IDENTITY_KEYS})
    backstory: str = ""
    values: list[str] = field(default_factory=list)
    speech: SpeechProfile = field(default_factory=SpeechProfile)
    emotion_map: list[EmotionRule] = field(default_factory=list)
    relationship_arc: dict[str, str] = field(default_factory=dict)
    boundaries: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    memory_hooks: list[str] = field(default_factory=list)
    taboos: list[str] = field(default_factory=list)
    legacy: bool = False                     # True 表示来自旧版 {persona, opening} 包装
    # 旧版原文（SPEC §5 未列出，属向后兼容的附加字段）：{"persona": str, "opening": str, ...}
    # 保留原文是为了「不丢信息」——旧角色的措辞在重新编译前必须能一字不差地取回来。
    legacy_raw: dict[str, str] = field(default_factory=dict)


@dataclass
class Critique:
    """一次人设自评的结果（6 维固定键 + 可执行问题清单）。"""

    scores: dict[str, int] = field(default_factory=lambda: {key: 0 for key in CRITIQUE_DIMENSIONS})
    issues: list[str] = field(default_factory=list)
    verdict: str = ""

    @property
    def average(self) -> int:
        """6 维综合分（0-100 四舍五入）。"""
        if not self.scores:
            return 0
        return int(round(sum(self.scores.values()) / len(self.scores)))

    @property
    def passed(self) -> bool:
        """是否达到上线门槛：综合分 ≥ PERSONA_PASS_SCORE 且没有待修问题。"""
        from core.config import PERSONA_PASS_SCORE

        return self.average >= PERSONA_PASS_SCORE and not self.issues


@dataclass
class HealthReport:
    """3 问人设体检报告：每项 {"name","ok","detail"}，外加总分与一句话结论。"""

    checks: list[dict[str, Any]] = field(default_factory=list)
    score: int = 0
    summary: str = ""


@dataclass
class CompileResult:
    """编译流水线的完整产物（UI 展示 + 答辩演示都读它）。"""

    card: PersonaCard = field(default_factory=PersonaCard)
    critique: Critique = field(default_factory=Critique)
    repair_rounds: int = 0
    health: HealthReport = field(default_factory=HealthReport)
    steps: list[str] = field(default_factory=list)
    used_llm: bool = False

    @property
    def degraded(self) -> bool:
        """是否走了降级路径（任一步 LLM 失败 / 用了描述兜底卡）。"""
        return any("降级" in step or "兜底" in step for step in self.steps)


# ---------------------------------------------------------------------------
# dict → dataclass（容错）
# ---------------------------------------------------------------------------


def dict_to_speech(raw: Any) -> SpeechProfile:
    """把任意 speech 片段清洗成 SpeechProfile。"""
    data = raw if isinstance(raw, dict) else {}
    return SpeechProfile(
        register=as_text(data.get("register"), limit=120),
        avg_sentence_len=as_int(data.get("avg_sentence_len"), 20, low=4, high=80),
        tics=as_text_list(data.get("tics"), limit=6, item_limit=24),
        punctuation=as_text(data.get("punctuation"), limit=80),
        banned_words=as_text_list(data.get("banned_words"), limit=8, item_limit=24),
        sample_lines=as_text_list(data.get("sample_lines"), limit=5, item_limit=120),
    )


def dict_to_emotion_map(raw: Any) -> list[EmotionRule]:
    """把任意 emotion_map 清洗成 EmotionRule 列表（3-5 条，非法项跳过而非报错）。"""
    rules: list[EmotionRule] = []
    if isinstance(raw, dict):  # 允许 {"难过": "...", "生气": "..."} 这种简写
        raw = [{"trigger": key, "reaction": value} for key, value in raw.items()]
    if not isinstance(raw, (list, tuple)):
        raw = [raw] if raw else []
    for item in raw:
        if isinstance(item, str):
            text = as_text(item, limit=120)
            if text:
                rules.append(EmotionRule(trigger=text, reaction="", body_language=""))
            continue
        if not isinstance(item, dict):
            continue
        trigger = as_text(item.get("trigger"), limit=60)
        reaction = as_text(item.get("reaction"), limit=120)
        body = as_text(item.get("body_language") or item.get("body"), limit=80)
        if trigger or reaction or body:
            rules.append(EmotionRule(trigger=trigger, reaction=reaction, body_language=body))
    return rules[:5]


def dict_to_relationship_arc(raw: Any) -> dict[str, str]:
    """把任意 relationship_arc 清洗成固定的四阶段字典（缺的阶段留空串）。"""
    arc: dict[str, str] = {}
    if isinstance(raw, (list, tuple)):
        # 允许模型直接给 4 条按顺序排列的字符串
        for stage, item in zip(CANONICAL_STAGES, raw):
            arc[stage] = as_text(item, limit=200)
    elif isinstance(raw, dict):
        for stage in CANONICAL_STAGES:
            arc[stage] = as_text(raw.get(stage), limit=200)
    else:
        text = as_text(raw, limit=200)
        if text:
            arc[CANONICAL_STAGES[0]] = text
    return {stage: arc.get(stage, "") for stage in CANONICAL_STAGES}


def dict_to_legacy_raw(raw: Any) -> dict[str, str]:
    """清洗旧版原文兜底字段（只保留字符串值，非字符串一律转文本）。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        text = value if isinstance(value, str) else as_text(value, limit=4000)
        if text:
            cleaned[key] = text
    return cleaned


def dict_to_card(raw: Any) -> PersonaCard:
    """把任意 dict（含脏数据）清洗成合法 PersonaCard，**永不抛异常**。

    LLM 可能返回缺字段、类型错、列表混入 None 的结果；旧版角色数据也只有
    persona/opening 两个字符串。这里逐字段降级，保证下游渲染与 UI 永远有稳定结构。
    """
    if isinstance(raw, PersonaCard):
        return raw
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if not data:
        return PersonaCard()

    identity_raw = data.get("identity")
    identity = {key: "" for key in IDENTITY_KEYS}
    if isinstance(identity_raw, dict):
        for key in IDENTITY_KEYS:
            identity[key] = as_text(identity_raw.get(key), limit=120)
    elif isinstance(identity_raw, str):
        identity["setting"] = as_text(identity_raw, limit=120)

    return PersonaCard(
        name=as_text(data.get("name"), limit=24),
        tagline=as_text(data.get("tagline") or data.get("slogan"), limit=60),
        avatar=as_text(data.get("avatar"), limit=8),
        identity=identity,
        backstory=as_text(data.get("backstory"), limit=1200),
        values=as_text_list(data.get("values"), limit=5, item_limit=40),
        speech=dict_to_speech(data.get("speech")),
        emotion_map=dict_to_emotion_map(data.get("emotion_map") or data.get("emotion_rules")),
        relationship_arc=dict_to_relationship_arc(data.get("relationship_arc")),
        boundaries=as_text_list(data.get("boundaries"), limit=6, item_limit=60),
        anti_patterns=as_text_list(data.get("anti_patterns"), limit=6, item_limit=60),
        memory_hooks=as_text_list(data.get("memory_hooks"), limit=6, item_limit=40),
        taboos=as_text_list(data.get("taboos"), limit=5, item_limit=60),
        legacy=bool(data.get("legacy", False)),
        legacy_raw=dict_to_legacy_raw(data.get("legacy_raw")),
    )


def dict_to_critique(raw: Any) -> Critique:
    """把 LLM 的自评输出清洗成 Critique（6 维缺失补 0，脏分数夹到 0-100）。"""
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    scores_raw = data.get("scores")
    if not isinstance(scores_raw, dict):
        scores_raw = {}
    scores = {key: as_int(scores_raw.get(key), 0, low=0, high=100) for key in CRITIQUE_DIMENSIONS}
    issues = as_text_list(data.get("issues"), limit=8, item_limit=200)
    verdict = as_text(data.get("verdict") or data.get("summary"), limit=300)
    return Critique(scores=scores, issues=issues, verdict=verdict)


# ---------------------------------------------------------------------------
# dataclass → dict（保证可 json.dumps）
# ---------------------------------------------------------------------------


def card_to_dict(card: PersonaCard) -> dict[str, Any]:
    """输出纯 Python 对象（str/int/list/dict），可安全 json.dumps 与缓存到磁盘。"""
    card = dict_to_card(card)  # 顺手做一次清洗，避免外部构造出半残对象
    return {
        "name": card.name,
        "tagline": card.tagline,
        "avatar": card.avatar,
        "identity": {key: card.identity.get(key, "") for key in IDENTITY_KEYS},
        "backstory": card.backstory,
        "values": list(card.values),
        "speech": {
            "register": card.speech.register,
            "avg_sentence_len": int(card.speech.avg_sentence_len),
            "tics": list(card.speech.tics),
            "punctuation": card.speech.punctuation,
            "banned_words": list(card.speech.banned_words),
            "sample_lines": list(card.speech.sample_lines),
        },
        "emotion_map": [
            {"trigger": rule.trigger, "reaction": rule.reaction, "body_language": rule.body_language}
            for rule in card.emotion_map
        ],
        "relationship_arc": {stage: card.relationship_arc.get(stage, "") for stage in CANONICAL_STAGES},
        "boundaries": list(card.boundaries),
        "anti_patterns": list(card.anti_patterns),
        "memory_hooks": list(card.memory_hooks),
        "taboos": list(card.taboos),
        "legacy": bool(card.legacy),
        "legacy_raw": dict(card.legacy_raw),
    }


def critique_to_dict(critique: Critique) -> dict[str, Any]:
    """自评结果转 dict（UI 展示用）。"""
    return {
        "scores": {key: int(critique.scores.get(key, 0)) for key in CRITIQUE_DIMENSIONS},
        "issues": list(critique.issues),
        "verdict": critique.verdict,
    }


def health_to_dict(report: HealthReport) -> dict[str, Any]:
    """体检报告转 dict（UI 展示用）。"""
    return {"checks": [dict(item) for item in report.checks], "score": int(report.score), "summary": report.summary}


# ---------------------------------------------------------------------------
# 结构校验（供体检与编译流程打日志用，不抛异常）
# ---------------------------------------------------------------------------


def validate_card(card: PersonaCard) -> list[str]:
    """返回人设卡的结构问题清单（中文、可直接展示）；空列表表示结构齐备。"""
    problems: list[str] = []
    if not card.name:
        problems.append("缺少角色名 name")
    if not card.backstory:
        problems.append("缺少 backstory（角色的来处）")
    if len(card.values) < 3:
        problems.append("values 少于 3 条，价值观不够可判断")
    if len(card.speech.sample_lines) < 3:
        problems.append("台词锚点不足 3 条，模型容易退化成通用助手")
    if not card.speech.tics:
        problems.append("speech.tics 为空，缺少口头禅")
    if not card.speech.banned_words:
        problems.append("speech.banned_words 为空，缺少「绝不这么说」的黑名单")
    if len(card.emotion_map) < 3:
        problems.append("emotion_map 少于 3 条，情绪反应不完整")
    if any(not card.relationship_arc.get(stage) for stage in CANONICAL_STAGES):
        problems.append("relationship_arc 未覆盖 陌生/熟悉/亲近/亲密 四阶段")
    if not card.boundaries:
        problems.append("缺少 boundaries 安全边界")
    if not card.anti_patterns:
        problems.append("缺少 anti_patterns（禁止的说话方式）")
    return problems
