"""检索打分、情绪门控、置顶兜底与中文记忆块渲染（SPEC §3.1 / §6）。

核心公式（可复算）：
``score = 0.45*sim + 0.20*importance + 0.20*recency + 0.15*emotion``
- ``sim``：字符 2-gram TF-IDF 余弦（:mod:`memory_os.textsim`）；
- ``recency``：``exp(-Δt_days/τ)``，Episode τ=10 天、Fact τ=30 天；
- ``emotion``：Episode 用 ``0.5*|valence| + 0.5*arousal``，Fact 固定 0.4；
- Fact 的 ``importance = confidence * 类别权重``，禁忌/约定额外 ``+0.12``。

除了打分，本模块还负责三件「不注入也不行」的事：
1. **情绪门控**：用户难过/生气且情绪激烈时，跳过相关度低的负能量旧事（写进 ``trace.skipped``）；
2. **置顶兜底**：身份/禁忌/约定类高置信事实 + 全部未闭环事件，不参与预算裁剪；
3. **预算裁剪**：记忆块 ≤ ``block_budget`` 字符，按 score 升序裁剪并写 ``trace.notes``。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from core.config import (
    ALL_TIERS,
    EPISODE_TAU_DAYS,
    FACT_CATEGORY_WEIGHT,
    FACT_PIN_CATEGORIES,
    FACT_PIN_CONFIDENCE,
    FACT_PIN_MAX,
    FACT_TAU_DAYS,
    LOOP_MAX,
    MOOD_GATE_AROUSAL,
    MOOD_GATE_SIM,
    MOOD_GATE_VALENCE,
    NEGATIVE_EMOTIONS,
    SHORT_TERM_INJECT_TURNS,
    TIER_LABELS,
    W_EMO,
    W_IMP,
    W_REC,
    W_SIM,
    MemoryConfig,
    clamp,
)
from memory_os import textsim
from memory_os.decay import last_touch, strength
from memory_os.schema import (
    Episode,
    Fact,
    MemoryBundle,
    MemoryTrace,
    OpenLoop,
    TraceItem,
    WorkingTurn,
    as_ms,
    days_between,
)

# 记忆块的五个小节标题（顺序即渲染顺序，SPEC §3.1）
SECTION_SHORT = "【短期·最近对话】"
SECTION_MID = "【中期·相关事件】"
SECTION_LONG = "【长期·稳定事实】"
SECTION_LOOPS = "【未闭环的事】"
SECTION_IMPRESSION = "【TA 对你的印象】"

# 负能量分类：用于判断「这条事实本身是否沉重」
HEAVY_CATEGORIES = frozenset({"厌恶", "禁忌"})
# 禁忌/约定的额外加权（SPEC §3.1）
BOOST_CATEGORIES = frozenset({"禁忌", "约定"})
PIN_BOOST = 0.12


# ---------------------------------------------------------------------------
# 时间展示（中文可读字符串，UI 直接渲染）
# ---------------------------------------------------------------------------


def _local(ts: float) -> time.struct_time:
    return time.localtime(as_ms(ts) / 1000.0)


def format_time_label(ts: float | None, now: float | None = None) -> str:
    """毫秒时间戳 → 中文可读字符串（如 ``11月4日 02:10`` / ``3 天前`` / ``今天 02:10``）。"""
    if not ts:
        return "时间未知"
    stamp = as_ms(ts)
    reference = as_ms(now)
    moment, today = _local(stamp), _local(reference)
    clock = f"{moment.tm_hour:02d}:{moment.tm_min:02d}"
    same_day = (moment.tm_year, moment.tm_yday) == (today.tm_year, today.tm_yday)
    if same_day:
        return f"今天 {clock}"
    delta_days = (as_ms(reference) - stamp) / 86_400_000.0
    if 0 < delta_days < 1.0:
        return f"昨天 {clock}"
    if 1.0 <= delta_days < 7.0:
        return f"{int(delta_days) + 1} 天前"
    return f"{moment.tm_mon}月{moment.tm_mday}日 {clock}"


def relative_label(ts: float | None, now: float | None = None) -> str:
    """毫秒时间戳 → 中文相对时间（如 ``3 天前``）；未来时间返回 ``刚刚``。"""
    if not ts:
        return "未知"
    delta_days = (as_ms(now) - as_ms(ts)) / 86_400_000.0
    if delta_days < 1.0:
        return "今天"
    if delta_days < 2.0:
        return "昨天"
    if delta_days < 30.0:
        return f"{int(delta_days)} 天前"
    return f"{int(delta_days / 30.0)} 个月前"


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """一条候选记忆及其打分明细（TraceItem 的原料）。"""

    kind: str
    item: Fact | Episode
    text: str
    sim: float
    importance: float
    recency: float
    emotion: float
    score: float
    pinned: bool = False
    reason: str = ""

    @property
    def item_id(self) -> str:
        """条目 id。"""
        return str(getattr(self.item, "id", ""))

    @property
    def tier(self) -> str:
        """所属记忆层：fact → long，episode → mid。"""
        return "long" if self.kind == "fact" else "mid"

    def to_trace(self) -> TraceItem:
        """转为 UI 可直接渲染的 TraceItem。"""
        return TraceItem(
            tier=self.tier,
            kind=self.kind,
            id=self.item_id,
            text=self.text,
            score=round(self.score, 4),
            components={
                "sim": round(self.sim, 4),
                "importance": round(self.importance, 4),
                "recency": round(self.recency, 4),
                "emotion": round(self.emotion, 4),
            },
            reason=self.reason,
            pinned=self.pinned,
        )


def fact_importance(fact: Fact) -> float:
    """事实的重要度 = 置信度 × 类别权重（SPEC §3.1）。"""
    weight = FACT_CATEGORY_WEIGHT.get(fact.category, 0.6)
    return clamp(fact.confidence * weight, 0.0, 1.0)


def _emotion_component(item: Fact | Episode) -> float:
    """情绪强度分量：Episode 用 valence/arousal，Fact 固定 0.4。"""
    if isinstance(item, Episode):
        return clamp(0.5 * abs(item.valence) + 0.5 * item.arousal, 0.0, 1.0)
    return 0.4


def _recency(item: Fact | Episode, now: float, config: MemoryConfig) -> float:
    """时间新鲜度 ``exp(-Δt/τ)``；τ 按 Fact / Episode 分别取。"""
    tau = config.fact_tau_days if isinstance(item, Fact) else config.episode_tau_days
    delta_days = max(0.0, days_between(now, last_touch(item)))
    return clamp(math.exp(-delta_days / max(0.1, tau)), 0.0, 1.0)


def _reason(candidate: Candidate, query: str, item: Fact | Episode) -> str:
    """生成一句中文召回理由（Trace 面板展示）。"""
    if candidate.pinned:
        return f"{item.category}类置顶事实" if isinstance(item, Fact) else "置顶情景"
    shared = textsim.common_terms(query, candidate.text)
    if shared and candidate.sim >= 0.2:
        return f"与提问相关（{'、'.join(shared)}）"
    if isinstance(item, Fact) and item.recall_count > 0:
        return f"曾被你反复提起（已召回 {item.recall_count} 次）"
    if candidate.recency >= 0.5:
        return "最近刚聊过"
    return "重要度较高"


def score_item(item: Fact | Episode, query: str, *, now: float,
               config: MemoryConfig) -> Candidate:
    """给单条记忆打分，返回带明细的 :class:`Candidate`。"""
    text = item.content if isinstance(item, Fact) else item.search_text
    sim = textsim.relevance(query, text)
    importance = (
        fact_importance(item) if isinstance(item, Fact) else clamp(item.importance, 0.0, 1.0)
    )
    recency = _recency(item, now, config)
    emotion = _emotion_component(item)
    weights = config.weights
    score = (
        weights.get("sim", W_SIM) * sim
        + weights.get("importance", W_IMP) * importance
        + weights.get("recency", W_REC) * recency
        + weights.get("emotion", W_EMO) * emotion
    )
    if isinstance(item, Fact) and item.category in BOOST_CATEGORIES:
        score += PIN_BOOST
    candidate = Candidate(
        kind="fact" if isinstance(item, Fact) else "episode",
        item=item,
        text=text,
        sim=sim,
        importance=importance,
        recency=recency,
        emotion=emotion,
        score=clamp(score, 0.0, 2.0),
    )
    candidate.reason = _reason(candidate, query, item)
    return candidate


def mood_gate_blocks(item: Fact | Episode, query: str, *, emotion: str,
                     config: MemoryConfig) -> bool:
    """情绪门控：用户低落且这条记忆本身沉重、又和当下无关时，不注入。"""
    if not getattr(config, "mood_gate", True):
        return False
    if emotion not in NEGATIVE_EMOTIONS:
        return False
    if isinstance(item, Episode):
        valence, arousal = item.valence, item.arousal
    elif item.category in HEAVY_CATEGORIES:
        valence, arousal = -0.45, 0.7  # 厌恶/禁忌天然沉重
    else:
        return False
    text = item.content if isinstance(item, Fact) else item.search_text
    return (
        valence < MOOD_GATE_VALENCE
        and arousal > MOOD_GATE_AROUSAL
        and textsim.relevance(query, text) < MOOD_GATE_SIM
    )


# ---------------------------------------------------------------------------
# 置顶与挑选
# ---------------------------------------------------------------------------


def pick_pinned_facts(facts: list[Fact], limit: int = FACT_PIN_MAX) -> list[Fact]:
    """挑出置顶事实：身份/禁忌/约定 且置信度达标，最多 ``limit`` 条。"""
    eligible = [
        fact
        for fact in facts
        if fact.active and fact.category in FACT_PIN_CATEGORIES and fact.confidence >= FACT_PIN_CONFIDENCE
    ]
    eligible.sort(key=lambda fact: (-fact_importance(fact), -fact.last_confirmed))
    return eligible[:limit]


def pick_open_loops(loops: list[OpenLoop], limit: int = LOOP_MAX) -> list[OpenLoop]:
    """挑出未闭环事件：有时间线索的优先，其次提及次数多的。"""
    live = [loop for loop in loops if loop.open and loop.what]
    live.sort(
        key=lambda loop: (
            0 if loop.due_hint else 1,
            -loop.mention_count,
            -(loop.created_at or 0.0),
        )
    )
    return live[:limit]


def style_directives_for(relationship: dict, *, stage: str, nickname: str | None,
                         limit: int = 4) -> list[str]:
    """根据关系档案生成「该怎么说话」的风格指令（人格漂移驱动）。"""
    intimacy = float(relationship.get("intimacy", 0.0) or 0.0)
    directives: list[str] = []
    if intimacy >= 75:
        directives.append("你们已经很亲近：可以直白地表达想念和关心，允许一点撒娇")
    elif intimacy >= 45:
        directives.append("你们已经熟悉：语气放松，可以开玩笑、用短句和语气词")
    elif intimacy >= 20:
        directives.append("你们还在互相熟悉：保持礼貌但别过于生分，多问一句")
    else:
        directives.append("你们刚认识：先照顾好当下的情绪，不要装熟、不要乱起昵称")
    if nickname:
        directives.append(f"称呼对方为「{nickname}」")
    jokes = relationship.get("inside_jokes") or []
    if jokes:
        # 「共同话题」已由 relationship_lines 用 shared_themes 输出，这里只留专属梗
        directives.append(f"可以自然地提起你们的梗：{jokes[0]}")
    return directives[:6]


@dataclass
class Ranked:
    """一轮检索的中间结果（候选 + 已淘汰项 + 备注）。"""

    facts: list[Candidate] = field(default_factory=list)
    episodes: list[Candidate] = field(default_factory=list)
    loops: list[OpenLoop] = field(default_factory=list)
    skipped: list[TraceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    budget: int = 0


def rank_items(role_memory, query: str, *, emotion: str, now: float,
               config: MemoryConfig, tiers: set[str] | None = None) -> Ranked:
    """对某角色的全部记忆打分排序，并按层 / 情绪门控做初筛。"""
    # tiers=None 表示「三层全要」；显式空集合表示「三层全关」，什么都不注入
    tiers = set(ALL_TIERS) if tiers is None else set(tiers)
    ranked = Ranked(budget=config.block_budget)
    if not tiers:
        ranked.notes.append("三层记忆全部关闭（tiers 为空），本轮不注入任何记忆")
        return ranked
    active_facts = [fact for fact in role_memory.facts if fact.active]
    active_episodes = [episode for episode in role_memory.episodes if not episode.archived]

    pinned_ids = {fact.id for fact in pick_pinned_facts(active_facts)}
    fact_candidates: list[Candidate] = []
    episode_candidates: list[Candidate] = []
    for fact in active_facts:
        candidate = score_item(fact, query, now=now, config=config)
        candidate.pinned = fact.id in pinned_ids
        fact_candidates.append(candidate)
    for episode in active_episodes:
        episode_candidates.append(score_item(episode, query, now=now, config=config))

    if "mid" not in tiers:
        ranked.notes.append("本轮不召回中期情景（tiers 未包含 mid）")
        episode_candidates = []
    else:
        kept: list[Candidate] = []
        for candidate in episode_candidates:
            if candidate.pinned or not mood_gate_blocks(
                candidate.item, query, emotion=emotion, config=config
            ):
                kept.append(candidate)
            else:
                ranked.skipped.append(
                    TraceItem(
                        tier="mid",
                        kind="episode",
                        id=candidate.item_id,
                        text=candidate.text,
                        score=round(candidate.score, 4),
                        components={
                            "sim": round(candidate.sim, 4),
                            "importance": round(candidate.importance, 4),
                            "recency": round(candidate.recency, 4),
                            "emotion": round(candidate.emotion, 4),
                        },
                        reason="情绪门控：当前情绪低落，且这条旧事与当下无关，先不翻出来",
                        pinned=False,
                    )
                )
                ranked.notes.append(f"情绪门控跳过 {len(ranked.skipped)} 条消极旧事")
        episode_candidates = kept

    if "long" not in tiers:
        # 消融开关对置顶事实同样生效：pinned 只影响排序/必带，不能绕过 tier 过滤
        ranked.notes.append("本轮不召回长期事实（tiers 未包含 long）")
        fact_candidates = []
    if "short" not in tiers:
        ranked.notes.append("本轮不注入短期对话（tiers 未包含 short）")

    fact_candidates.sort(key=lambda c: (-c.score, c.item_id))
    episode_candidates.sort(key=lambda c: (-c.score, c.item_id))
    ranked.facts = fact_candidates
    ranked.episodes = episode_candidates
    ranked.loops = pick_open_loops(role_memory.open_loops, config.loop_max)
    if ranked.skipped:
        # 备注去重，保持顺序
        ranked.notes = list(dict.fromkeys(ranked.notes))
    return ranked


# ---------------------------------------------------------------------------
# 记忆块渲染
# ---------------------------------------------------------------------------


def relationship_lines(relationship: dict, now: float | None = None) -> list[str]:
    """把关系档案渲染成「TA 对你的印象」小节的中文条目。"""
    intimacy = float(relationship.get("intimacy", 0.0) or 0.0)
    trust = float(relationship.get("trust", 0.0) or 0.0)
    stage = str(relationship.get("stage") or "陌生")
    lines = [f"关系：{stage}（亲密度 {intimacy:.0f}/100、信任 {trust:.0f}/100）"]
    nickname = relationship.get("nickname")
    if nickname:
        lines.append(f"称呼：TA 希望你叫「{nickname}」")
    themes = relationship.get("shared_themes") or []
    if themes:
        lines.append(f"共同话题：{'、'.join(themes[:3])}")
    jokes = relationship.get("inside_jokes") or []
    if jokes:
        lines.append(f"你们的梗：{jokes[0]}")
    turn_count = int(relationship.get("turn_count", 0) or 0)
    if turn_count:
        lines.append(f"你们已经聊过 {turn_count} 轮")
    return lines


def _truncate(text: str, limit: int) -> str:
    """按字符数截断并补省略号（中文按字符算长度）。"""
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _short_line(turn: WorkingTurn, now: float) -> str:
    """渲染一条短期对话。"""
    speaker = "用户" if turn.role == "user" else "TA"
    clock = format_time_label(turn.ts, now).replace("今天 ", "")
    suffix = f"（{turn.emotion}）" if turn.emotion else ""
    return f"· {speaker} {clock}：{_truncate(turn.content, 60)}{suffix}"


def _fact_line(candidate: Candidate, now: float) -> str:
    """渲染一条长期事实（带分类与置信度来源标记）。"""
    fact = candidate.item
    assert isinstance(fact, Fact)
    pin = "★" if candidate.pinned else ""
    return (
        f"· {pin}[{fact.category}] {_truncate(fact.content, 48)}"
        f"（置信 {fact.confidence:.2f}）"
    )


def _episode_line(candidate: Candidate, now: float) -> str:
    """渲染一条中期情景（带日期与相关度来源标记）。"""
    episode = candidate.item
    assert isinstance(episode, Episode)
    when = format_time_label(episode.created_at, now)
    mood = f"·{episode.valence:+.1f}" if abs(episode.valence) >= 0.3 else ""
    return f"· {when}｜{_truncate(episode.title, 16)}：{_truncate(episode.summary, 72)}{mood}"


def _loop_line(loop: OpenLoop) -> str:
    """渲染一条未闭环事件。"""
    due = f"（{loop.due_hint}）" if loop.due_hint else ""
    extra = f"｜已提 {loop.mention_count} 次" if loop.mention_count > 1 else ""
    return f"· [{loop.kind}] {_truncate(loop.what, 40)}{due}{extra}"


def render_block(*, working_turns: list[WorkingTurn], working_summary: str,
                 facts: list[Candidate], episodes: list[Candidate],
                 loops: list[OpenLoop], impression: list[str], now: float) -> str:
    """把各部分拼成给 LLM 的中文记忆块（空小节不渲染）。"""
    sections: list[str] = []
    short_lines: list[str] = []
    if working_summary:
        short_lines.append(f"· 早前摘要：{_truncate(working_summary, 80)}")
    short_lines.extend(_short_line(turn, now) for turn in working_turns)
    if short_lines:
        sections.append("\n".join([SECTION_SHORT, *short_lines]))
    if episodes:
        sections.append("\n".join([SECTION_MID, *[_episode_line(c, now) for c in episodes]]))
    if facts:
        sections.append("\n".join([SECTION_LONG, *[_fact_line(c, now) for c in facts]]))
    if loops:
        sections.append("\n".join([SECTION_LOOPS, *[_loop_line(loop) for loop in loops]]))
    if impression:
        sections.append(
            "\n".join([SECTION_IMPRESSION, *[f"· {_truncate(line, 60)}" for line in impression]])
        )
    return "\n".join(sections).strip()


@dataclass
class Selection:
    """预算裁剪后的最终注入集合与记忆块正文。"""

    facts: list[Candidate] = field(default_factory=list)
    episodes: list[Candidate] = field(default_factory=list)
    loops: list[OpenLoop] = field(default_factory=list)
    working_turns: list[WorkingTurn] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    block: str = ""


def select_and_render(ranked: Ranked, *, working_turns: list[WorkingTurn],
                      working_summary: str, impression: list[str], now: float,
                      config: MemoryConfig) -> Selection:
    """按 top-k 取候选，再按预算裁掉低分条目，返回最终注入集合。"""
    selection = Selection(
        facts=list(ranked.facts[: config.fact_top_k]),
        episodes=list(ranked.episodes[: config.episode_top_k]),
        loops=list(ranked.loops),
        working_turns=list(working_turns[: config.short_term_inject_turns]),
        notes=list(ranked.notes),
    )
    dropped = 0
    while True:
        selection.block = render_block(
            working_turns=selection.working_turns,
            working_summary=working_summary,
            facts=selection.facts,
            episodes=selection.episodes,
            loops=selection.loops,
            impression=impression,
            now=now,
        )
        if len(selection.block) <= config.block_budget:
            break
        droppable = [c for c in selection.facts if not c.pinned]
        if droppable:
            victim = min(droppable, key=lambda c: (c.score, c.item_id))
            selection.facts.remove(victim)
        elif selection.episodes:
            victim = min(selection.episodes, key=lambda c: (c.score, c.item_id))
            selection.episodes.remove(victim)
        else:
            selection.notes.append(
                f"记忆块仍超预算（{len(selection.block)} 字符），已无低分条目可裁剪"
            )
            break
        dropped += 1
    if dropped:
        selection.notes.append(
            f"预算裁剪：记忆块超过 {config.block_budget} 字符，按分数升序移除了 {dropped} 条低分记忆"
        )
    return selection


__all__ = [
    "Candidate",
    "HEAVY_CATEGORIES",
    "PIN_BOOST",
    "Ranked",
    "SECTION_IMPRESSION",
    "SECTION_LONG",
    "SECTION_LOOPS",
    "SECTION_MID",
    "SECTION_SHORT",
    "Selection",
    "TIER_LABELS",
    "fact_importance",
    "format_time_label",
    "mood_gate_blocks",
    "pick_open_loops",
    "pick_pinned_facts",
    "rank_items",
    "relationship_lines",
    "relative_label",
    "render_block",
    "score_item",
    "select_and_render",
    "style_directives_for",
]
