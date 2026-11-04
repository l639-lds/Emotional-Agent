"""记忆固化（consolidate）：把短期对话压成中期情景 + 长期事实 + 未闭环事件。

严格按 SPEC §3.2 的七步走：
1. 取 ``turns[last_consolidated_turn:]``，不足 ``consolidate_min_turns*2`` 且非 force 就不做；
2. **一次** LLM 调用（``purpose="consolidate"``，``demo_context`` 按 §4.1 填全）；
3. 逐字段降级：某字段类型不对只丢该字段；LLM 抛异常则用 :func:`memory_os.prompts.heuristic_extract` 兜底；
4. Fact 合并 / 冲突消解（``superseded_by``）/ 共享作用域提升；
5. OpenLoop 关闭与合并（同一件事只累计 ``mention_count``）；
6. 关系档案推进（亲密度只增不减、阶段重算、昵称留痕）；
7. 推进游标并写中文备注。
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Callable

from core.config import (
    CANONICAL_STAGES,
    FACT_CATEGORY_WEIGHT,
    FACT_SHARED_CATEGORIES,
    NEGATIVE_EMOTIONS,
    MemoryConfig,
    clamp,
    stage_of,
)
from memory_os import prompts, textsim
from memory_os.schema import (
    ConsolidationResult,
    Episode,
    Fact,
    OpenLoop,
    RoleMemory,
    as_ms,
    normalize_category,
    normalize_scope,
)

JsonCaller = Callable[..., dict[str, Any]]

# 否定词与反义对（用于冲突消解）
_NEGATIONS = ("不", "没", "别", "无", "不再", "停止")
_ANTONYM_PAIRS = (("喜欢", "讨厌"), ("喜欢", "厌恶"), ("爱", "恨"), ("支持", "反对"))
_ATTR_RE = re.compile(r"用户(住在|来自|的名字叫|叫|在)([^\s，。；,;、]{1,8})")

# 冲突消解后新事实的置信度（SPEC §3.2 第 4 步）
CONFLICT_CONFIDENCE = 0.75


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def pending_turn_count(memory: RoleMemory) -> int:
    """返回尚未固化的 turn 条数。"""
    start = max(0, min(memory.last_consolidated_turn, len(memory.turns)))
    return max(0, len(memory.turns) - start)


def _text(raw: Any, limit: int = 120) -> str | None:
    """把值收敛成去空白的字符串；类型不对返回 None（逐字段降级）。"""
    if isinstance(raw, str):
        clean = raw.strip()
        return clean[:limit] if clean else None
    return None


def _num(raw: Any, default: float, low: float, high: float) -> float:
    """把值收敛成区间内的浮点数；类型不对用默认值。"""
    try:
        return clamp(float(raw), low, high)
    except (TypeError, ValueError):
        return default


def _num_or_none(raw: Any) -> float | None:
    """可选数值字段：类型不对就丢弃该字段。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _dict_list(raw: Any, key: str) -> list[dict[str, Any]] | None:
    """把 ``[dict]`` 或 ``[str]`` 统一成 dict 列表；不是列表时返回 None。"""
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append({key: item.strip()})
    return out


def _supports_kwarg(fn: Callable[..., Any], name: str) -> bool:
    """判断函数是否接受某个关键字参数（兼容 ``**kwargs``）。"""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    if name in parameters:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _default_caller() -> JsonCaller:
    """默认走 :func:`core.llm.call_json`（全项目唯一 LLM 入口）。"""
    from core.llm import call_json

    return call_json


def _as_payload(raw: Any) -> dict[str, Any]:
    """把模型输出收敛成 dict（兼容字符串 JSON 与代码围栏）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text, strict=False)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def call_llm_json(llm: JsonCaller | None, system_prompt: str, user_prompt: str, *,
                  purpose: str, demo_context: dict[str, Any],
                  max_tokens: int = 1600) -> dict[str, Any]:
    """发起一次结构化调用；自动适配 caller 是否支持 ``demo_context``。"""
    caller = llm or _default_caller()
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "purpose": purpose,
        "max_tokens": max_tokens,
    }
    if _supports_kwarg(caller, "demo_context"):
        kwargs["demo_context"] = demo_context
    try:
        return _as_payload(caller(**kwargs))
    except TypeError:
        # 某些极简 caller 不接受 demo_context / max_tokens，降级重试一次
        kwargs.pop("demo_context", None)
        kwargs.pop("max_tokens", None)
        return _as_payload(caller(**kwargs))


# ---------------------------------------------------------------------------
# 冲突判定
# ---------------------------------------------------------------------------


def _strip_negations(text: str) -> str:
    """去掉否定词，便于比较「正反表述是否指同一件事」。"""
    for word in _NEGATIONS:
        text = text.replace(word, "")
    return text


def _has_negation(text: str) -> bool:
    """文本里是否出现否定词。"""
    return any(word in text for word in _NEGATIONS)


def detect_contradiction(old_text: str, new_text: str) -> str | None:
    """判断两条同分类事实是否语义矛盾；返回中文原因，无矛盾返回 None。"""
    old_norm = textsim.normalize(old_text)
    new_norm = textsim.normalize(new_text)
    if not old_norm or not new_norm:
        return None
    # 1) 同一属性的取值不同（住在 / 来自 / 叫 …）
    old_attr, new_attr = _ATTR_RE.search(old_norm), _ATTR_RE.search(new_norm)
    if old_attr and new_attr and old_attr.group(1) == new_attr.group(1):
        if old_attr.group(2) and new_attr.group(2) and old_attr.group(2) != new_attr.group(2):
            return f"同一属性的取值不同（{old_attr.group(2)} → {new_attr.group(2)}）"
    # 2) 否定词差异：去掉否定词后是同一句话
    stripped_old, stripped_new = _strip_negations(old_norm), _strip_negations(new_norm)
    if _has_negation(old_norm) != _has_negation(new_norm):
        if stripped_old == stripped_new or textsim.similarity(stripped_old, stripped_new) >= 0.6:
            return "否定词差异（正反表述冲突）"
    # 3) 喜好反转：喜欢 ↔ 讨厌
    for left, right in _ANTONYM_PAIRS:
        if (left in old_norm and right in new_norm and right not in old_norm) or (
            right in old_norm and left in new_norm and left not in old_norm
        ):
            return f"喜好反转（{left} ↔ {right}）"
    return None


# ---------------------------------------------------------------------------
# 第 4 步：事实
# ---------------------------------------------------------------------------


def _best_match(content: str, category: str, facts: list[Fact]) -> tuple[Fact | None, float]:
    """在同分类的生效事实里找最相似的一条。"""
    best: Fact | None = None
    best_sim = 0.0
    for fact in facts:
        if not fact.active or fact.category != category:
            continue
        score = textsim.similarity(content, fact.content)
        if score > best_sim:
            best, best_sim = fact, score
    return best, best_sim


def apply_facts(memory: RoleMemory, entries: list[dict[str, Any]], *, now: float,
                config: MemoryConfig, result: ConsolidationResult,
                origin: str = "llm") -> list[Fact]:
    """执行 SPEC §3.2 第 4 步：合并 / 冲突消解 / 新增，返回新增的事实。"""
    created: list[Fact] = []
    for entry in entries:
        content = _text(entry.get("content"), 120)
        if not content or len(content) < 2:
            result.notes.append("丢弃一条空事实")
            continue
        category = normalize_category(entry.get("category"))
        confidence = _num(entry.get("confidence"), 0.6, 0.0, 1.0)
        scope = normalize_scope(entry.get("scope"), category, FACT_SHARED_CATEGORIES)
        match, similarity = _best_match(content, category, memory.facts)

        if match is not None and similarity > config.fact_merge_sim:
            match.evidence_count += 1
            match.last_confirmed = now
            match.confidence = clamp(match.confidence + 0.1, 0.0, 1.0)
            if len(content) > len(match.content):
                match.content = content
            if scope == "shared":
                match.scope = "shared"
            memory.facts[memory.facts.index(match)] = match
            result.updated_facts.append(match.content)
            continue

        if match is not None and similarity >= config.fact_conflict_sim:
            reason = detect_contradiction(match.content, content)
            if reason:
                fresh = Fact(
                    content=content,
                    category=category,
                    confidence=CONFLICT_CONFIDENCE,
                    first_seen=now,
                    last_confirmed=now,
                    scope=scope,
                    origin=origin,
                )
                if getattr(config, "conflict_resolution", True):
                    match.superseded_by = fresh.id
                    result.superseded.append(
                        {
                            "old": match.id,
                            "new": fresh.id,
                            "reason": reason,
                            "old_text": match.content,
                            "new_text": fresh.content,
                        }
                    )
                else:
                    result.notes.append(
                        f"冲突消解已关闭：未标记「{match.content[:16]}」被取代（消融实验）"
                    )
                memory.facts.append(fresh)
                created.append(fresh)
                result.new_facts.append(fresh.content)
                continue

        fresh = Fact(
            content=content,
            category=category,
            confidence=confidence,
            first_seen=now,
            last_confirmed=now,
            scope=scope,
            origin=origin,
        )
        memory.facts.append(fresh)
        created.append(fresh)
        result.new_facts.append(fresh.content)
    return created


def upgrade_shared_scope(memory: RoleMemory, *, min_support: int = 2,
                         min_importance: float = 0.75) -> list[Fact]:
    """多条情景共同支撑的客观事实升级为 ``shared``（跨角色共享）。"""
    upgraded: list[Fact] = []
    live_episodes = [episode for episode in memory.episodes if not episode.archived]
    for fact in memory.facts:
        if not fact.active or fact.scope == "shared":
            continue
        if fact.category not in FACT_SHARED_CATEGORIES:
            continue
        importance = fact.confidence * FACT_CATEGORY_WEIGHT.get(fact.category, 0.6)
        if importance < min_importance:
            continue
        support = sum(
            1
            for episode in live_episodes
            if textsim.similarity(fact.content, episode.search_text) >= 0.25
        )
        if support >= min_support:
            fact.scope = "shared"
            upgraded.append(fact)
    return upgraded


# ---------------------------------------------------------------------------
# 第 5 步：未闭环事件
# ---------------------------------------------------------------------------


def _find_open_loop(memory: RoleMemory, entry: dict[str, Any]) -> OpenLoop | None:
    """按 id 或文本相似度在未闭环事件里找目标。"""
    loop_id = _text(entry.get("id"), 40)
    if loop_id:
        for loop in memory.open_loops:
            if loop.id == loop_id:
                return loop
    what = _text(entry.get("what"), 80)
    if not what:
        return None
    best: OpenLoop | None = None
    best_sim = 0.0
    for loop in memory.open_loops:
        if not loop.open:
            continue
        similarity = textsim.similarity(what, loop.what)
        if what in loop.what or loop.what in what:
            similarity = max(similarity, 0.9)
        if similarity > best_sim:
            best, best_sim = loop, similarity
    return best if best_sim >= 0.6 else None


def apply_loops(memory: RoleMemory, open_entries: list[dict[str, Any]],
                close_entries: list[dict[str, Any]], *, now: float,
                config: MemoryConfig, result: ConsolidationResult) -> None:
    """执行 SPEC §3.2 第 5 步：关闭已闭环事件、合并重复事件、新建事件。"""
    for entry in close_entries:
        target = _find_open_loop(memory, entry)
        if target is None or not target.open:
            result.notes.append(f"未找到要关闭的未闭环事件：{_text(entry.get('what'), 20) or '（无描述）'}")
            continue
        target.status = "closed"
        target.closed_at = now
        result.loops_closed.append(target.what)
    for entry in open_entries:
        what = _text(entry.get("what"), 80)
        if not what:
            result.notes.append("丢弃一条空的未闭环事件")
            continue
        kind = _text(entry.get("kind"), 8) or "待办"
        due_hint = _text(entry.get("due_hint"), 20)
        existing = _find_open_loop(memory, entry)
        if existing is not None and (
            textsim.similarity(what, existing.what) > config.loop_merge_sim
            or what in existing.what
            or existing.what in what
        ):
            existing.mention_count += 1
            if due_hint:
                existing.due_hint = due_hint
            continue
        loop = OpenLoop(
            what=what, kind=kind, due_hint=due_hint, created_at=now, mention_count=1
        )
        memory.open_loops.append(loop)
        result.loops_opened.append(loop.what)


# ---------------------------------------------------------------------------
# 第 6 步：关系档案
# ---------------------------------------------------------------------------


def update_relationship(memory: RoleMemory, payload: dict[str, Any], *,
                        user_turn_count: int, negative_tone: bool, now: float,
                        config: MemoryConfig) -> dict[str, Any]:
    """按亲密度递增规则推进关系阶段，返回 ``{"before":…, "after":…}``。"""
    relationship = memory.relationship
    before = {
        "intimacy": round(relationship.intimacy, 2),
        "trust": round(relationship.trust, 2),
        "stage": relationship.stage,
        "nickname": relationship.nickname,
    }

    base = min(3.0, config.intimacy_per_turn * max(0, user_turn_count))
    console = config.intimacy_console_bonus if negative_tone else 0.0
    delta = _num_or_none(payload.get("intimacy_delta"))
    delta = 0.0 if delta is None else clamp(delta, 0.0, 3.0)
    relationship.intimacy = clamp(relationship.intimacy + base + console + delta, 0.0, 100.0)

    trust_delta = _num_or_none(payload.get("trust_delta"))
    if trust_delta is not None:
        relationship.trust = clamp(relationship.trust + clamp(trust_delta, -2.0, 2.0), 0.0, 100.0)

    relationship.stage = stage_of(relationship.intimacy)
    if CANONICAL_STAGES.index(relationship.stage) < CANONICAL_STAGES.index(before["stage"]):
        relationship.stage = before["stage"]  # 阶段只进不退

    nickname = _text(payload.get("nickname"), 12)
    if nickname and nickname != relationship.nickname:
        # 首次得到称呼也记一条历史，UI 才能显示「以前怎么叫、现在怎么叫」
        relationship.nickname_history.append(
            {"value": relationship.nickname or "（未起昵称）", "at": now}
        )
        relationship.nickname = nickname
        relationship.nickname_history = relationship.nickname_history[-10:]

    theme_raw = payload.get("themes")
    if not isinstance(theme_raw, list):
        theme_raw = payload.get("shared_themes")
    if not isinstance(theme_raw, list):
        single_theme = _text(payload.get("shared_theme"), 12)
        theme_raw = [single_theme] if single_theme else []
    for item in theme_raw:
        theme = _text(item, 12)
        if theme and theme not in relationship.shared_themes:
            relationship.shared_themes.append(theme)
    relationship.shared_themes = relationship.shared_themes[-8:]

    joke = _text(payload.get("inside_joke"), 20)
    if joke and joke not in relationship.inside_jokes:
        relationship.inside_jokes.insert(0, joke)
        relationship.inside_jokes = relationship.inside_jokes[:5]

    # turn_count 由 engine.record_turn 实时递增（UI 需要立即显示），此处不再累加
    relationship.updated_at = now
    after = {
        "intimacy": round(relationship.intimacy, 2),
        "trust": round(relationship.trust, 2),
        "stage": relationship.stage,
        "nickname": relationship.nickname,
    }
    return {"before": before, "after": after}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _build_episode(raw: Any, *, turn_start: int, turn_end: int, now: float,
                   fallback: dict[str, Any], notes: list[str]) -> Episode:
    """按「逐字段降级」构造情景记忆。"""
    source = raw if isinstance(raw, dict) else {}
    if not source:
        notes.append("LLM 未返回可用 episode，已用规则摘要兜底")
    defaults = fallback if isinstance(fallback, dict) else {}

    def pick(key: str) -> Any:
        value = source.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return defaults.get(key)
        return value

    title = _text(pick("title"), 60) or f"第 {turn_start + 1}-{turn_end} 条对话"
    summary = _text(pick("summary"), 200) or "用户进行了一段对话"
    topics = pick("topics")
    topics = topics if isinstance(topics, list) else defaults.get("topics", [])
    keywords = pick("keywords")
    keywords = keywords if isinstance(keywords, list) else defaults.get("keywords", topics)
    return Episode(
        title=title,
        summary=summary,
        topics=topics,
        keywords=keywords,
        valence=_num(pick("valence"), 0.0, -1.0, 1.0),
        arousal=_num(pick("arousal"), 0.5, 0.0, 1.0),
        importance=_num(pick("importance"), 0.5, 0.0, 1.0),
        turn_start=turn_start,
        turn_end=turn_end,
        created_at=now,
        last_recalled_at=now,
    )


def run_consolidation(memory: RoleMemory, *, llm: JsonCaller | None = None,
                      config: MemoryConfig | None = None,
                      now: float | None = None,
                      force: bool = False) -> ConsolidationResult:
    """执行一次完整固化（原地修改 ``memory``），返回可展示的固化结果。"""
    cfg = config or MemoryConfig()
    stamp = as_ms(now)
    start = max(0, min(memory.last_consolidated_turn, len(memory.turns)))
    new_turns = memory.turns[start:]
    min_turns = max(1, cfg.consolidate_min_turns * 2)

    if not new_turns:
        return ConsolidationResult(
            done=False, notes=["没有待固化的对话，本次跳过"], used_llm=False
        )
    if len(new_turns) < min_turns and not force:
        return ConsolidationResult(
            done=False,
            notes=[f"新增 {len(new_turns)} 条对话，不足 {min_turns} 条阈值，本次跳过"],
            used_llm=False,
        )

    turn_payload = [{"role": turn.role, "content": turn.content} for turn in new_turns]
    user_turns = [turn for turn in new_turns if turn.role == "user"]
    nickname = memory.relationship.nickname
    demo_context = {
        "turns": turn_payload,
        "fallback_summary": memory.working_summary,
        "nickname": nickname,
    }
    user_prompt = prompts.build_consolidate_user_prompt(
        turn_payload,
        [
            {"content": fact.content, "category": fact.category}
            for fact in memory.facts
            if fact.active
        ][:8],
        [{"what": loop.what, "kind": loop.kind} for loop in memory.open_loops if loop.open][:5],
        nickname,
    )

    result = ConsolidationResult(done=True)
    heuristic = prompts.heuristic_extract(turn_payload, fallback_summary=memory.working_summary)
    try:
        payload = call_llm_json(
            llm,
            prompts.CONSOLIDATE_SYSTEM,
            user_prompt,
            purpose="consolidate",
            demo_context=demo_context,
        )
        result.used_llm = True
        if not payload:
            raise ValueError("模型返回了空 JSON")
    except Exception as exc:  # LLM 不可用 / 输出不可解析 → 规则兜底
        payload = {}
        result.used_llm = False
        result.notes.append(f"LLM 调用失败已降级为规则兜底：{str(exc)[:120]}")
    if not any(key in payload for key in ("episode", "facts", "loops_open")):
        if result.used_llm:
            result.notes.append("LLM 输出缺少记忆字段，已降级为规则兜底")
        payload = heuristic
        result.used_llm = False

    episode = _build_episode(
        payload.get("episode"),
        turn_start=start,
        turn_end=len(memory.turns),
        now=stamp,
        fallback=heuristic.get("episode", {}),
        notes=result.notes,
    )
    memory.episodes.append(episode)
    result.episode_id = episode.id

    raw_facts = _dict_list(payload.get("facts"), "content")
    fact_entries = raw_facts if raw_facts is not None else heuristic.get("facts", [])
    apply_facts(
        memory,
        fact_entries,
        now=stamp,
        config=cfg,
        result=result,
        origin="llm" if result.used_llm else "heuristic",
    )

    upgraded = upgrade_shared_scope(memory)
    if upgraded:
        result.notes.append(f"{len(upgraded)} 条客观事实升级为跨角色共享：{'、'.join(f.content[:12] for f in upgraded)}")

    raw_open = _dict_list(payload.get("loops_open"), "what")
    open_entries = raw_open if raw_open is not None else heuristic.get("loops_open", [])
    close_entries = _dict_list(payload.get("loops_close"), "what") or []
    apply_loops(memory, open_entries, close_entries, now=stamp, config=cfg, result=result)

    relationship_payload = payload.get("relationship")
    relationship_payload = relationship_payload if isinstance(relationship_payload, dict) else {}
    relationship_payload = dict(relationship_payload)
    if payload.get("nickname") is not None:
        relationship_payload.setdefault("nickname", payload.get("nickname"))
    negative_tone = any(turn.emotion in NEGATIVE_EMOTIONS for turn in new_turns)
    result.relationship = update_relationship(
        memory,
        relationship_payload,
        user_turn_count=len(user_turns),
        negative_tone=negative_tone,
        now=stamp,
        config=cfg,
    )

    memory.last_consolidated_turn = len(memory.turns)
    memory.summary_until_turn = memory.last_consolidated_turn
    titles = [item.title for item in memory.episodes[-3:]]
    memory.working_summary = "；".join(titles)[:200]

    before, after = result.relationship.get("before", {}), result.relationship.get("after", {})
    result.notes.insert(0, f"固化 {len(new_turns)} 条对话（用户 {len(user_turns)} 轮）")
    result.notes.append(f"生成情景「{episode.title}」")
    result.notes.append(
        f"事实：新增 {len(result.new_facts)} 条、更新 {len(result.updated_facts)} 条、"
        f"冲突消解 {len(result.superseded)} 条"
    )
    result.notes.append(
        f"未闭环：新增 {len(result.loops_opened)} 条、关闭 {len(result.loops_closed)} 条"
    )
    result.notes.append(
        f"关系：{before.get('stage', '陌生')} → {after.get('stage', '陌生')}"
        f"（亲密度 {before.get('intimacy', 0.0):.1f} → {after.get('intimacy', 0.0):.1f}）"
    )
    result.notes.append("数据来源：LLM" if result.used_llm else "数据来源：规则兜底")
    return result


def close_loop_by_id(memory: RoleMemory, loop_id: str, *,
                     now: float | None = None) -> bool:
    """手动关闭一个未闭环事件；找不到或已关闭返回 False。"""
    stamp = as_ms(now)
    for loop in memory.open_loops:
        if loop.id == loop_id and loop.open:
            loop.status = "closed"
            loop.closed_at = stamp
            return True
    return False


__all__ = [
    "CONFLICT_CONFIDENCE",
    "apply_facts",
    "apply_loops",
    "call_llm_json",
    "close_loop_by_id",
    "detect_contradiction",
    "pending_turn_count",
    "run_consolidation",
    "update_relationship",
    "upgrade_shared_scope",
]
