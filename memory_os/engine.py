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
                if text:
                    hooks.append({"kind": "recent", "text": text[:24], "detail": "刚才说的话"})
                break
        return hooks[: max(1, limit + 3)]

    def proactive_openers(self, role: str, *, limit: int = 3,
                          now: float | None = None) -> list[str]:
        """生成主动关怀开场白：优先未闭环事件，其次最近话题/长期事实，最后才日常寒暄。

        每条素材只出一句（LLM 措辞或模板二选一），跨条按相似度去重，
        所以返回的是"不同事情的开口话术"，而不是同一句的几种说法。
        """
        as_ms(now)  # 保留时间注入，当前不参与选择，只保证调用契约一致
        memory = self._role_memory(role)
        hooks = self._proactive_hooks(memory, limit=max(1, limit))
        payload: dict[str, Any] = {}
        if hooks:
            try:
                payload = call_llm_json(
                    self.llm,
                    prompts.PROACTIVE_SYSTEM,
                    prompts.build_proactive_user_prompt(hooks),
                    purpose="proactive",
                    demo_context={
                        "hooks": hooks,
                        "open_loops": [{"what": hook["text"]} for hook in hooks if hook["kind"] == "loop"],
                    },
                    max_tokens=400,
                )
            except Exception:  # 主动关怀失败不能影响主流程
                payload = {}
        llm_opener = _clip(payload.get("opener"), 80)
        openers: list[str] = []
        for index, hook in enumerate(hooks):
            kind = hook["kind"]
            text = hook["text"]
            if kind == "loop":
                template = f"上次你提到{text}，后来怎么样了？"
            elif kind == "episode":
                template = f"上次你聊到{text}，后来有变化吗？"
            elif kind == "recent":
                template = f"你刚说的「{text}」，我还记着——现在怎么样了？"
            else:
                template = f"我还记得你说过{text}，最近还好吗？"
            opener = template
            if index == 0 and llm_opener and textsim.similarity(llm_opener, template) < 0.8:
                opener = llm_opener
            if all(textsim.similarity(opener, kept) < 0.8 for kept in openers):
                openers.append(opener)
            if len(openers) >= limit:
                break
        if not openers:
            openers.append(DEFAULT_OPENER)
        return openers[: max(1, limit)]

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


__all__ = ["DEFAULT_OPENER", "MemoryEngine", "fact_snapshot_item", "normalize_emotion"]
