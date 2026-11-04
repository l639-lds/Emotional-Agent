"""记忆引擎：五层记忆的唯一公开门面（SPEC §3 + §8.3 的 API 集合）。

写路径：``record_turn`` → ``consolidate`` → ``decay_pass``；读路径：``retrieve`` → ``build_memory_block``；
L0 原文库走 ``search_archive``，L3 快照走 ``list_snapshots``，L4 自我认知走 ``self_notes``。
所有时间都通过 ``now``（毫秒时间戳）注入，**不读系统时钟做业务判断**，因此单测可完全确定性复现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.config import (
    ALLOWED_EMOTIONS,
    CANONICAL_STAGES,
    EMOTION_ALIASES,
    EMOTION_INTENSITY,
    FACT_SHARED_CATEGORIES,
    MemoryConfig,
    clamp,
)
from memory_os import decay as decay_mod
from memory_os import layers, prompts, retrieval, textsim
from memory_os.consolidate import (
    JsonCaller,
    call_llm_json,
    close_loop_by_id,
    pending_turn_count,
    run_consolidation,
)
from memory_os.decay import strength
from memory_os.schema import (
    ConsolidationResult,
    Episode,
    Fact,
    MemoryBundle,
    MemoryTrace,
    RoleMemory,
    WorkingTurn,
    as_ms,
    get_role_memory,
    get_shared_facts,
    now_ms,
    set_role_memory,
)
from memory_os.store import MemoryStore

# 通用开场白（没有任何未闭环事件时的兜底）
DEFAULT_OPENER = "今天过得怎么样？有什么想聊聊的吗？"

# 主动开口的"句型"：真人不会每次都用疑问句。有时只是提一句（陈述），有时关心/提醒，
# 有时说说自己的感受，有时干脆不提问、就在旁边坐着。全部问句会显得像客服回访。
PROACTIVE_STYLES: tuple[str, ...] = ("ask", "recall", "care", "self", "tease", "presence")
STYLE_LABELS: dict[str, str] = {
    "ask": "问一句",
    "recall": "提起旧事",
    "care": "关心一句",
    "self": "说说自己",
    "tease": "打趣",
    "presence": "就在旁边",
}
# 关系越近，能用的句型越多：刚认识就打趣、或讲自己的感受，会显得越界。
_STYLE_MIN_STAGE: dict[str, int] = {
    "ask": 0,
    "care": 0,
    "presence": 0,
    "recall": 1,
    "tease": 2,
    "self": 2,
}

_ASK_LINES = {
    "loop": "上次你提到{text}，后来怎么样了？",
    "episode": "上次聊到{text}，后来有变化吗？",
    "recent": "你刚说的「{text}」，现在怎么样了？",
    "fact": "你说过{text}——最近还顺吗？",
}
_RECALL_LINES = {
    "loop": "我还记着你说过{text}。不急，想说的时候再说。",
    "episode": "上次聊到{text}那天，你说话很慢，我记着呢。",
    "recent": "你刚说的「{text}」，我记住了。",
    "fact": "我记得你说过{text}。",
}
_CARE_LINES = {
    "loop": "{text}那件事，别一个人扛着。",
    "episode": "上次聊完{text}，那天你睡得好吗？",
    "recent": "「{text}」——先顾好你自己，别硬撑。",
    "fact": "你说过{text}，记得按时吃饭。",
}
_TEASE_LINES = {
    "loop": "{text}？我还没忘呢。先说好，今天不许硬撑。",
    "episode": "又聊到{text}了。你这人，记性比我还好。",
    "recent": "「{text}」——又来。行，我陪你。",
    "fact": "{text}？这习惯我算是摸清了。",
}
_STYLE_LINES: dict[str, dict[str, str]] = {
    "ask": _ASK_LINES,
    "recall": _RECALL_LINES,
    "care": _CARE_LINES,
    "tease": _TEASE_LINES,
}
_PRESENCE_LINES = (
    "我在。想说话就说，不想说就坐着。",
    "不用急着说什么，我在旁边。",
)
_SELF_LINES = (
    "说个我自己的事——{note}",
    "我最近才想明白：{note}",
)


def _opener_template(style: str, hook: dict[str, str] | None, *, self_note: str = "") -> str:
    """按「句型 + 素材」拼一句话（LLM 不可用时的兜底，也是判断语气是否走偏的参照）。"""
    text = str((hook or {}).get("text") or "").strip()
    kind = str((hook or {}).get("kind") or "")
    if style == "presence":
        return _PRESENCE_LINES[0]
    if style == "self":
        if not self_note:
            return ""
        return _SELF_LINES[0].format(note=self_note.rstrip("。！？.!?"))
    if not text:
        return ""
    table = _STYLE_LINES.get(style) or {}
    line = table.get(kind) or table.get("fact") or ""
    return line.format(text=text)


def _fits_style(text: str, style: str) -> bool:
    """粗略校验 LLM 写的这句话配不配得上要的句型（问句/陈述句不能错位）。"""
    body = (text or "").strip()
    if not body:
        return False
    is_question = body.endswith(("？", "?")) or "？" in body[-3:]
    if style == "ask":
        return is_question
    return not is_question


def _style_reason(style: str, hook: dict[str, str] | None) -> str:
    """给 UI 的一句注解：这句话为什么这么开口。"""
    kind = str((hook or {}).get("kind") or "")
    source = {
        "loop": "未闭环事件",
        "episode": "最近聊过的话题",
        "fact": "长期事实",
        "recent": "刚才那句话",
    }.get(kind, "")
    if style == "presence":
        return "没有具体素材时，不硬找话题"
    if style == "self":
        return "用 L4 自我认知说一句关于自己的话"
    return f"{STYLE_LABELS.get(style, style)}（素材：{source or '无'}）"


# 「你刚说的「你好呀，我叫小林」」这种开场白很怪：先把纯寒暄/自我介绍的口头语剥掉，
# 剩下的如果还不够长（<6 字），就宁可不用这条素材。
_SMALLTALK_PREFIXES = (
    "你好呀", "你好", "您好", "在吗", "在不在", "喂", "嗨", "hi", "hello", "嗯嗯", "嗯", "哦",
    "那个", "我说", "话说", "对了", "其实", "就是", "然后", "而且", "所以", "麻烦你", "请问",
)
_SMALLTALK_SUFFIXES = ("哦", "呀", "啊", "呢", "吧", "啦", "哈", "嘛")


def _strip_smalltalk(text: str) -> str:
    """剥掉句首寒暄与结尾语气词，用来判断"这句话值不值得拿来开口"。"""
    clean = " ".join(str(text or "").split())
    changed = True
    while changed:
        changed = False
        lowered = clean.lower()
        for prefix in _SMALLTALK_PREFIXES:
            if lowered.startswith(prefix):
                clean = clean[len(prefix) :].lstrip("，,、 　:：")
                changed = True
                break
    for suffix in _SMALLTALK_SUFFIXES:
        if clean.endswith(suffix) and len(clean) > len(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean.strip()


def _second_person(text: str) -> str:
    """长期事实是第三人称存的（「用户在下周三有面试」）；拿来当开场白要换成「你」。"""
    clean = str(text or "").strip()
    for old, new in (("用户", "你"), ("ta的", "你的"), ("TA的", "你的"), ("ta", "你"), ("TA", "你")):
        clean = clean.replace(old, new)
    return clean.strip()


def _short_clause(text: str, limit: int = 12) -> str:
    """只取第一个分句，并截到 ``limit`` 字——引号里塞一整句话会很吵。"""
    clean = str(text or "").strip()
    for mark in ("，", "。", "！", "？", "；", "、", ",", ".", "!", "?", ";"):
        head, sep, _ = clean.partition(mark)
        if sep and head.strip():
            clean = head.strip()
            break
    clean = clean.strip("「」“”\"' ")
    return clean[:limit]


def normalize_emotion(raw: Any) -> str:
    """把任意情绪写法归一为四类之一，未知回落到「平静」。"""
    emotion = str(raw).strip() if raw is not None else "平静"
    emotion = EMOTION_ALIASES.get(emotion, emotion)
    return emotion if emotion in ALLOWED_EMOTIONS else "平静"


def _clip(text: Any, limit: int = 80) -> str | None:
    """把值收敛成截断后的非空字符串；类型不对返回 None。"""
    if isinstance(text, str):
        clean = " ".join(text.split())
        return clean[:limit] if clean else None
    return None


def fact_snapshot_item(fact: Fact, *, now: float | None = None,
                       config: MemoryConfig | None = None) -> dict[str, Any]:
    """把一条 Fact 渲染成 UI / 快照用的 dict（SPEC §6 的 ``long.items`` 形状）。"""
    cfg = config or MemoryConfig()
    stamp = as_ms(now)
    return {
        "id": fact.id,
        "content": fact.content,
        "category": fact.category,
        "confidence": round(fact.confidence, 3),
        "evidence_count": fact.evidence_count,
        "strength": round(
            strength(fact, stamp, episode_tau=cfg.episode_tau_days,
                     fact_tau=cfg.fact_tau_days),
            3,
        ),
        "last_confirmed_label": retrieval.format_time_label(fact.last_confirmed, stamp),
        "archived": fact.archived,
        "superseded_by": fact.superseded_by,
    }


class MemoryEngine:
    """记忆引擎：一个实例服务一个记忆文件，内部按角色隔离。"""

    def __init__(self, store: MemoryStore, *, llm: JsonCaller | None = None,
                 config: MemoryConfig | None = None) -> None:
        """绑定存储与 LLM；``llm`` 为 None 时走 :func:`core.llm.call_json`。"""
        self.store = store
        self.llm = llm
        self.config = config or MemoryConfig()
        self.last_consolidation: ConsolidationResult | None = None
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        """当前记忆文件的内存视图（懒加载，改动请走引擎方法）。"""
        if self._data is None:
            self._data = self.store.load()
        return self._data

    def reload(self) -> dict[str, Any]:
        """丢弃内存视图，强制重新读盘（外部改过文件时调用）。"""
        self._data = self.store.load()
        return self._data

    def save(self) -> None:
        """把当前内存视图原子写回磁盘。"""
        self.store.save(self.data)

    def _role_memory(self, role: str) -> RoleMemory:
        """读取某角色的记忆（返回可 mutate 的对象，需配合 :meth:`_commit`）。"""
        return get_role_memory(self.data, role)

    def _commit(self, role: str, memory: RoleMemory) -> None:
        """写回某角色记忆并落盘。"""
        set_role_memory(self.data, role, memory)
        self.store.save(self.data)

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------
    def record_turn(self, role: str, user_text: str, assistant_text: str, emotion: str,
                    *, now: float | None = None) -> ConsolidationResult | None:
        """记录一轮对话；到点自动触发固化（``auto_consolidate`` 可关）。

        Returns:
            本次触发的固化结果；未触发则返回 None。
        """
        stamp = as_ms(now)
        feeling = normalize_emotion(emotion)
        memory = self._role_memory(role)
        salience = EMOTION_INTENSITY.get(feeling, 0.35)
        user_clean = (user_text or "").strip()
        assistant_clean = (assistant_text or "").strip()
        if user_clean:
            memory.turns.append(
                WorkingTurn(
                    role="user", content=user_clean, ts=stamp,
                    emotion=feeling, salience=salience,
                )
            )
        if assistant_clean:
            memory.turns.append(
                WorkingTurn(
                    role="assistant", content=assistant_clean, ts=stamp,
                    emotion=feeling, salience=clamp(salience * 0.6, 0.0, 1.0),
                )
            )
        memory.turn_counter += 1
        memory.relationship.turn_count += 1
        memory.relationship.updated_at = stamp
        limit = max(2, self.config.short_term_max_turns)
        if len(memory.turns) > limit:
            overflow = len(memory.turns) - limit
            memory.turns = memory.turns[-limit:]
            # last_consolidated_turn 是 turns 的下标，裁剪后必须同步左移
            memory.last_consolidated_turn = max(0, memory.last_consolidated_turn - overflow)
            memory.summary_until_turn = max(0, memory.summary_until_turn - overflow)
        self._commit(role, memory)
        if getattr(self.config, "auto_consolidate", True) and self.should_consolidate(role):
            return self.consolidate(role, now=stamp)
        return None

    def should_consolidate(self, role: str) -> bool:
        """是否到了该固化的时机（未固化 turn 达标且轮数到阈值）。"""
        memory = self._role_memory(role)
        pending = pending_turn_count(memory)
        if pending < max(1, self.config.consolidate_min_turns * 2):
            return False
        every = max(1, self.config.consolidate_every_turns)
        return memory.turn_counter > 0 and memory.turn_counter % every == 0

    def pending_turns(self, role: str) -> int:
        """返回尚未固化的 turn 条数（供 UI 显示「还有 N 条没整理」）。"""
        return pending_turn_count(self._role_memory(role))

    def consolidate(self, role: str, *, force: bool = False,
                    now: float | None = None) -> ConsolidationResult:
        """执行一次「睡眠整理」，结果同时写入 :attr:`last_consolidation`。

        真正发生固化时会额外写一份 L3 快照（``data/archive/<角色>/<id>.json``）。
        """
        stamp = as_ms(now)
        memory = self._role_memory(role)
        result = run_consolidation(
            memory,
            llm=self.llm,
            config=self.config,
            now=stamp,
            force=force,
            role=role,
            archive_dir=self.archive_dir,
        )
        if result.done:
            self._commit(role, memory)
        self.last_consolidation = result
        return result

    @property
    def archive_dir(self) -> Path:
        """L3 快照的落盘根目录（记忆文件同级的 ``archive/``）。"""
        return layers.archive_root(self.store.path)

    def close_loop(self, role: str, loop_id: str, *, now: float | None = None) -> bool:
        """手动关闭一个未闭环事件；成功返回 True。"""
        stamp = as_ms(now)
        memory = self._role_memory(role)
        if close_loop_by_id(memory, loop_id, now=stamp):
            self._commit(role, memory)
            return True
        return False

    def reset_role(self, role: str) -> None:
        """清空某个角色的全部记忆（对话记录与三层记忆一起清）。"""
        data = self.data
        roles = data.setdefault("roles", {})
        chats = data.setdefault("chats", {})
        roles.pop(role, None)
        chats.pop(role, None)
        self.last_consolidation = None
        self.store.save(data)

    def decay_pass(self, role: str, *, now: float | None = None) -> dict:
        """跑一次遗忘：只把弱记忆标成 ``archived``，返回 ``{archived, weakest}``。"""
        stamp = as_ms(now)
        memory = self._role_memory(role)
        items = [*memory.episodes, *memory.facts]
        result = decay_mod.decay_pass(
            items,
            stamp,
            self.config.archive_threshold,
            episode_tau=self.config.episode_tau_days,
            fact_tau=self.config.fact_tau_days,
        )
        self._commit(role, memory)
        return result

    def correct_fact(self, role: str, fact_id: str, new_content: str, *,
                     category: str | None = None,
                     now: float | None = None) -> dict | None:
        """用户亲手纠正一条事实（L2）：新事实高置信，旧事实 ``superseded_by`` 留档。

        Returns:
            新事实的 UI 形状 dict（同 :meth:`snapshot_for_ui` 的 long 条目）；找不到返回 None。
        """
        stamp = as_ms(now)
        memory = self._role_memory(role)
        corrected = layers.apply_correction(
            memory, fact_id, new_content, category=category, now=stamp, config=self.config
        )
        if corrected is None:
            return None
        self._commit(role, memory)
        return fact_snapshot_item(corrected, now=stamp, config=self.config)

    # ------------------------------------------------------------------
    # 读路径
    # ------------------------------------------------------------------
    def retrieve(self, role: str, query: str, emotion: str = "平静", *,
                 tiers: set[str] | None = None,
                 include_self: bool = False,
                 now: float | None = None) -> MemoryBundle:
        """按 SPEC §3.1 公式检索三层记忆，返回带可解释 Trace 的 MemoryBundle。

        Args:
            include_self: 是否把 L4 自我认知带进 bundle（只在会话首轮由 UI 传 True）。
        """
        stamp = as_ms(now)
        feeling = normalize_emotion(emotion)
        memory = self._role_memory(role)
        ranked = retrieval.rank_items(
            memory, query, emotion=feeling, now=stamp, config=self.config, tiers=tiers
        )
        relationship_dict = memory.relationship.to_dict()
        directives = retrieval.style_directives_for(
            relationship_dict,
            stage=memory.relationship.stage,
            nickname=memory.relationship.nickname,
        )
        impression = [*retrieval.relationship_lines(relationship_dict), *directives[:2]]
        include_short = tiers is None or "short" in set(tiers)
        all_off = tiers is not None and not set(tiers)  # 三层全关：一条都不注入
        if all_off:
            impression = []
        selection = retrieval.select_and_render(
            ranked,
            working_turns=memory.turns if include_short else [],
            working_summary=memory.working_summary if include_short else "",
            impression=impression,
            now=stamp,
            config=self.config,
        )
        # 命中即强化（间隔重复）：recall_count+1、last_recalled=now、base_strength+0.1
        for candidate in [*selection.facts, *selection.episodes]:
            decay_mod.reinforce(candidate.item, stamp)
        if selection.facts or selection.episodes:
            self._commit(role, memory)

        trace_items = [candidate.to_trace() for candidate in [*selection.facts, *selection.episodes]]
        bundle = MemoryBundle(
            role=role,
            relationship=relationship_dict,
            facts=[c.to_trace() for c in selection.facts],
            episodes=[c.to_trace() for c in selection.episodes],
            loops=list(selection.loops),
            working_turns=list(selection.working_turns),
            working_summary=memory.working_summary if include_short else "",
            trace=MemoryTrace(
                items=trace_items,
                skipped=list(ranked.skipped),
                notes=list(selection.notes),
                budget=self.config.block_budget,
            ),
            style_directives=directives,
            block=selection.block,
            include_self=include_self,
            self_notes=(
                layers.self_note_payloads(
                    memory, limit=self.config.self_note_inject_max, now=stamp
                )
                if include_self
                else []
            ),
        )
        return bundle

    def build_memory_block(self, bundle: MemoryBundle, *, include_self: bool = False) -> str:
        """把 MemoryBundle 渲染成注入 system prompt 的中文记忆块（无多余空行）。

        ``include_self=True`` 时在末尾追加「【它的自我叙述】」一节（最多 3 条，绝不出现 "None"）。
        """
        want_self = include_self or bool(getattr(bundle, "include_self", False))
        self_section = (
            layers.render_self_section(getattr(bundle, "self_notes", []) or [])
            if want_self
            else ""
        )
        text = self._base_block(bundle)
        if self_section:
            text = f"{text}\n\n{self_section}" if text else self_section
        return text.strip()

    def _base_block(self, bundle: MemoryBundle) -> str:
        """渲染三层记忆本体（不含 L4 自我叙述小节）。"""
        if bundle.block:
            return bundle.block.strip()
        if not (bundle.facts or bundle.episodes or bundle.working_turns or bundle.loops):
            # 没有任何命中（例如 tiers 全关）：连"印象"小节也不注入，返回空串
            return ""
        # 兜底：bundle 由外部构造（没有预渲染 block）时，按 trace 顺序重新拼一次
        fallback_facts = [
            retrieval.Candidate(
                kind="fact", item=Fact(content=item.text), text=item.text,
                sim=item.components.get("sim", 0.0),
                importance=item.components.get("importance", 0.0),
                recency=item.components.get("recency", 0.0),
                emotion=item.components.get("emotion", 0.0),
                score=item.score,
            )
            for item in bundle.facts
        ]
        fallback_episodes = [
            retrieval.Candidate(
                kind="episode",
                item=Episode(title=item.text, summary=item.text),
                text=item.text,
                sim=item.components.get("sim", 0.0),
                importance=item.components.get("importance", 0.0),
                recency=item.components.get("recency", 0.0),
                emotion=item.components.get("emotion", 0.0),
                score=item.score,
            )
            for item in bundle.episodes
        ]
        impression = [*retrieval.relationship_lines(bundle.relationship), *bundle.style_directives[:2]]
        return retrieval.render_block(
            working_turns=bundle.working_turns,
            working_summary=bundle.working_summary,
            facts=fallback_facts,
            episodes=fallback_episodes,
            loops=bundle.loops,
            impression=impression,
            now=now_ms(),
        ).strip()

    def _proactive_hooks(self, memory: Any, *, limit: int = 3) -> list[dict]:
        """收集"可以拿来开口"的素材：未闭环事件 > 最近聊过的话题 > 长期事实。

        这是"主动关怀"能不能说人话的关键：只说 ``今天过得怎么样`` 等于没有记忆；
        有了 hooks，开场白就能落在"上次你说的面试""你提过的失眠"这种事上。
        """
        hooks: list[dict] = []
        for loop in retrieval.pick_open_loops(memory.open_loops, limit):
            text = str(getattr(loop, "what", "") or "").strip()
            if text:
                hooks.append({"kind": "loop", "text": text,
                              "detail": str(getattr(loop, "due_hint", "") or "")})
        episodes = sorted(
            memory.episodes,
            key=lambda item: float(getattr(item, "created_at", 0) or 0),
            reverse=True,
        )
        for episode in episodes[:2]:
            if getattr(episode, "archived", False):
                continue
            topics = [str(topic) for topic in (getattr(episode, "topics", None) or []) if str(topic).strip()]
            text = " · ".join(topics[:2]) or str(getattr(episode, "title", "") or "").strip()
            if text:
                hooks.append({"kind": "episode", "text": text,
                              "detail": str(getattr(episode, "title", "") or "")})
        fact_categories = {"近况", "长期目标", "喜好", "重要人物"}
        for fact in memory.facts:
            if not getattr(fact, "active", False):
                continue
            if str(getattr(fact, "category", "")) not in fact_categories:
                continue
            text = str(getattr(fact, "content", "") or "").strip()
            text = _short_clause(_second_person(text), 16)
            if text:
                hooks.append({"kind": "fact", "text": text,
                              "detail": str(getattr(fact, "category", ""))})
            if sum(1 for hook in hooks if hook["kind"] == "fact") >= 2:
                break
        if not hooks:
            # 还没有固化过记忆时，至少能接住"你刚才说的那句话"，避免一开口就是空泛寒暄
            for turn in reversed(memory.turns):
                if str(getattr(turn, "role", "")) != "user":
                    continue
                text = str(getattr(turn, "content", "") or "").strip()
                text = text.rstrip("。！？.!?，,、；;")
                text = _strip_smalltalk(text)
                text = _short_clause(text, 12)
                if text and len(text) >= 6:
                    hooks.append({"kind": "recent", "text": text, "detail": "刚才说的话"})
                break
        return hooks[: max(1, limit + 3)]

    def _stage_index(self, memory: RoleMemory) -> int:
        """关系阶段 → 0..3；认不出来的阶段按「陌生」处理。"""
        stage = str(getattr(memory.relationship, "stage", "") or "")
        try:
            return CANONICAL_STAGES.index(stage)
        except ValueError:
            return 0

    def _latest_self_note(self, memory: RoleMemory) -> str:
        """L4 里最新的一条自我叙述（让 TA 也能说一句"关于自己"的话）。"""
        notes = sorted(
            memory.self_notes,
            key=lambda note: float(getattr(note, "updated_at", 0) or 0),
            reverse=True,
        )
        for note in notes:
            text = str(getattr(note, "content", "") or "").strip()
            if text:
                return text
        return ""

    def _available_styles(self, memory: RoleMemory) -> list[str]:
        """按关系阶段筛句型：刚认识不打趣、也不讲自己的感受。"""
        level = self._stage_index(memory)
        styles = [style for style in PROACTIVE_STYLES if _STYLE_MIN_STAGE.get(style, 0) <= level]
        if not self._latest_self_note(memory):
            styles = [style for style in styles if style != "self"]
        return styles

    def _last_style(self, role: str) -> str:
        meta = self.data.get("meta")
        styles = meta.get("proactive_last_style") if isinstance(meta, dict) else None
        return str(styles.get(role) or "") if isinstance(styles, dict) else ""

    def _remember_style(self, role: str, style: str) -> None:
        """记住这次用了哪种句型：下一轮换一种，别每次都同一个开头。"""
        if not style:
            return
        meta = self.data.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.data["meta"] = meta
        styles = meta.get("proactive_last_style")
        if not isinstance(styles, dict):
            styles = {}
            meta["proactive_last_style"] = styles
        if styles.get(role) != style:
            styles[role] = style
            self.save()

    def proactive_suggestions(self, role: str, *, limit: int = 3,
                              now: float | None = None,
                              polish: bool = True,
                              commit: bool = True) -> list[dict[str, Any]]:
        """候选「下一句」：每句带句型（问 / 提旧事 / 关心 / 说自己 / 打趣 / 就陪着）。

        同一批里句型不重复，首句避开上一轮用过的句型；素材按「未闭环 → 最近话题 →
        长期事实 → 刚才那句」挑。所以它既能落在具体的事上，也不会永远在提问。

        Args:
            polish: 是否让模型把模板润色成 TA 的口吻。界面上"先给你看看"时传 False，
                省掉一次模型调用；真要用这句话时再传 True。
            commit: 是否把这次用的句型记进 ``meta``（下一轮据此换一种）。
                只是渲染候选时传 False，避免"页面每刷新一次就换一句"。
        """
        as_ms(now)  # 保留时间注入，当前只用于契约一致
        memory = self._role_memory(role)
        hooks = self._proactive_hooks(memory, limit=max(2, limit))
        styles = self._available_styles(memory)
        if not styles:
            return [{
                "opener": DEFAULT_OPENER,
                "style": "presence",
                "label": STYLE_LABELS["presence"],
                "hook": "",
                "hook_kind": "",
                "reason": "还没有可参考的素材",
            }]

        last = self._last_style(role)
        if last in styles and len(styles) > 1:
            pivot = styles.index(last) + 1
            styles = styles[pivot:] + styles[:pivot]

        note = self._latest_self_note(memory)
        candidates: list[dict[str, Any]] = []
        used_hooks: set[str] = set()
        for style in styles:
            hook: dict[str, str] | None = None
            if style in _STYLE_LINES:
                hook = next((item for item in hooks if item["text"] not in used_hooks), None)
                if hook is None:
                    continue
            template = _opener_template(style, hook, self_note=note)
            if not template:
                continue
            if hook is not None:
                used_hooks.add(hook["text"])
            candidates.append({
                "opener": template,
                "style": style,
                "label": STYLE_LABELS[style],
                "hook": str((hook or {}).get("text") or ""),
                "hook_kind": str((hook or {}).get("kind") or ""),
                "reason": _style_reason(style, hook),
            })
            if len(candidates) >= max(1, limit):
                break

        if not candidates:
            candidates.append({
                "opener": DEFAULT_OPENER,
                "style": "presence",
                "label": STYLE_LABELS["presence"],
                "hook": "",
                "hook_kind": "",
                "reason": "没有可用素材",
            })

        polished = self._polish_opener(candidates[0], hooks, note, last) if polish else ""
        if polished:
            candidates[0]["opener"] = polished

        picked: list[dict[str, Any]] = []
        for item in candidates:
            if all(textsim.similarity(item["opener"], kept["opener"]) < 0.8 for kept in picked):
                picked.append(item)
        if not picked:
            picked = candidates[:1]
        if commit:
            self._remember_style(role, str(picked[0]["style"]))
        return picked[: max(1, limit)]

    def _polish_opener(self, candidate: dict[str, Any], hooks: list[dict[str, Any]],
                       self_note: str = "", last_style: str = "") -> str:
        """让模型把模板润色成 TA 的口气；语气走偏（该陈述却写成问句）就不用它。"""
        if not hooks and not self_note:
            return ""
        try:
            payload = call_llm_json(
                self.llm,
                prompts.PROACTIVE_SYSTEM,
                prompts.build_proactive_user_prompt(
                    hooks,
                    style=str(candidate.get("style") or ""),
                    style_hint=str(candidate.get("label") or ""),
                    last_style=last_style,
                ),
                purpose="proactive",
                demo_context={
                    "hooks": hooks,
                    "style": candidate.get("style"),
                    "self_note": self_note,
                    "open_loops": [{"what": hook["text"]} for hook in hooks if hook["kind"] == "loop"],
                },
                max_tokens=400,
            )
        except Exception:  # 主动关怀失败不能影响主流程
            return ""
        text = _clip(payload.get("opener"), 80) or ""
        if not text or not _fits_style(text, str(candidate.get("style") or "")):
            return ""
        if textsim.similarity(text, str(candidate.get("opener") or "")) >= 0.95:
            return ""
        return text

    def proactive_openers(self, role: str, *, limit: int = 3,
                          now: float | None = None) -> list[str]:
        """只要文本的旧接口（脚本/测试沿用）：内部走 :meth:`proactive_suggestions`。"""
        return [item["opener"] for item in self.proactive_suggestions(role, limit=limit, now=now)]

    # ------------------------------------------------------------------
    # L0 原文库 / L3 快照 / L4 自我认知
    # ------------------------------------------------------------------
    def search_archive(self, role: str, query: str, *, limit: int = 5,
                       now: float | None = None) -> list[dict]:
        """在 L0 原文库（全量历史对话）里检索，返回按相似度降序的命中列表。"""
        cap = limit if limit and limit > 0 else self.config.archive_top_k
        return layers.search_archive(self.data, role, query, limit=cap, now=now)

    def archive_stats(self, role: str) -> dict:
        """L0 原文库规模统计：``{messages, user_messages, first_label, last_label}``。"""
        return layers.archive_stats(self.data, role)

    def self_notes(self, role: str, *, limit: int = 3) -> list[dict]:
        """L4 自我认知：最近的若干条自我叙述（按更新时间倒序）。"""
        memory = self._role_memory(role)
        return layers.self_note_payloads(memory, limit=limit)

    def list_snapshots(self, role: str, *, limit: int = 5) -> list[dict]:
        """L3 固化快照索引（按时间倒序）。"""
        memory = self._role_memory(role)
        return layers.list_snapshot_payloads(memory, limit=limit)

    # ------------------------------------------------------------------
    # UI 快照
    # ------------------------------------------------------------------
    def stats(self, role: str) -> dict:
        """统计某角色的记忆规模（UI 顶部指标卡）。"""
        return self._stats(self._role_memory(role), now_ms())

    def _stats(self, memory: RoleMemory, stamp: float) -> dict:
        """按 SPEC §6 的键名计算统计值。"""
        config = self.config
        active_facts = [fact for fact in memory.facts if fact.active]
        archived = sum(1 for fact in memory.facts if fact.archived)
        archived += sum(1 for episode in memory.episodes if episode.archived)
        active_episodes = [episode for episode in memory.episodes if not episode.archived]
        values = [
            strength(item, stamp, episode_tau=config.episode_tau_days,
                     fact_tau=config.fact_tau_days)
            for item in [*active_episodes, *active_facts]
        ]
        return {
            "turns": len(memory.turns),
            "episodes": len(memory.episodes),
            "facts": len(active_facts),
            "shared_facts": len(get_shared_facts(self.data)),
            "open_loops": sum(1 for loop in memory.open_loops if loop.open),
            "archived": archived,
            "avg_strength": round(sum(values) / len(values), 3) if values else 0.0,
        }

    def snapshot_for_ui(self, role: str, *, now: float | None = None) -> dict:
        """导出 UI 渲染所需的完整快照（键名严格对齐 SPEC §6）。"""
        stamp = as_ms(now)
        config = self.config
        memory = self._role_memory(role)
        relationship = memory.relationship

        def item_strength(item: Any) -> float:
            return round(
                strength(item, stamp, episode_tau=config.episode_tau_days,
                         fact_tau=config.fact_tau_days),
                3,
            )

        short = {
            "turns": [
                {
                    "role": turn.role,
                    "content": turn.content,
                    "ts_label": retrieval.format_time_label(turn.ts, stamp),
                    "emotion": turn.emotion or "平静",
                }
                for turn in memory.turns
            ],
            "summary": memory.working_summary,
            "count": len(memory.turns),
        }
        mid = {
            "items": [
                {
                    "id": episode.id,
                    "title": episode.title,
                    "summary": episode.summary,
                    "topics": list(episode.topics),
                    "valence": round(episode.valence, 3),
                    "arousal": round(episode.arousal, 3),
                    "importance": round(episode.importance, 3),
                    "strength": item_strength(episode),
                    "recall_count": episode.recall_count,
                    "created_label": retrieval.format_time_label(episode.created_at, stamp),
                    "archived": episode.archived,
                }
                for episode in memory.episodes
            ]
        }

        def fact_item(fact: Fact) -> dict:
            return fact_snapshot_item(fact, now=stamp, config=config)

        long_term = {
            "items": [fact_item(fact) for fact in memory.facts],
            "shared": [fact_item(fact) for fact in get_shared_facts(self.data)],
        }
        relationship_view = {
            "intimacy": round(relationship.intimacy, 2),
            "trust": round(relationship.trust, 2),
            "stage": relationship.stage,
            "nickname": relationship.nickname,
            "nickname_history": [dict(item) for item in relationship.nickname_history],
            "inside_jokes": list(relationship.inside_jokes),
            "shared_themes": list(relationship.shared_themes),
            "turn_count": relationship.turn_count,
        }
        loops = [
            {
                "id": loop.id,
                "what": loop.what,
                "kind": loop.kind,
                "due_hint": loop.due_hint,
                "status": loop.status,
                "mention_count": loop.mention_count,
                "created_label": retrieval.format_time_label(loop.created_at, stamp),
            }
            for loop in memory.open_loops
        ]
        curve = decay_mod.decay_curve(
            memory.episodes,
            memory.facts,
            stamp,
            14,
            episode_tau=config.episode_tau_days,
            fact_tau=config.fact_tau_days,
        )
        self_view = {
            "items": layers.self_note_payloads(
                memory, limit=max(1, config.self_note_max), now=stamp
            ),
            "count": len(memory.self_notes),
        }
        archive_view = layers.archive_stats(self.data, role, now=stamp)
        snapshot_view = {
            "items": [
                {
                    "id": snap.id,
                    "label": snap.label,
                    "episode_title": snap.episode_title,
                    "facts_added": snap.facts_added,
                    "facts_updated": snap.facts_updated,
                }
                for snap in sorted(memory.snapshots, key=lambda item: item.created_at,
                                   reverse=True)
            ],
            "count": len(memory.snapshots),
        }
        return {
            "short": short,
            "mid": mid,
            "long": long_term,
            "relationship": relationship_view,
            "loops": loops,
            "stats": self._stats(memory, stamp),
            "decay_curve": curve,
            "self": self_view,
            "archive": archive_view,
            "snapshots": snapshot_view,
        }


__all__ = [
    "DEFAULT_OPENER",
    "PROACTIVE_STYLES",
    "STYLE_LABELS",
    "MemoryEngine",
    "fact_snapshot_item",
    "normalize_emotion",
]
