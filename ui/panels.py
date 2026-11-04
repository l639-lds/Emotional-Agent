"""记忆面板渲染：三层记忆、关系档案、未闭环事件、遗忘曲线、可解释 Trace。

只依赖 `snapshot_for_ui` / `MemoryBundle` 的字段，不直接读写磁盘。
"""

from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd
import streamlit as st

from core import config as cfg
from memory_os import MemoryEngine
from ui import state

_TIER_COLOR = {"short": "#38BDF8", "mid": "#A78BFA", "long": "#F59E0B"}
_TIER_ALIAS = {"short": "短期", "mid": "中期", "long": "长期"}


def fmt_ts(value: Any) -> str:
    """毫秒时间戳 → "11-04 02:10"；非法值返回空串。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(value) / 1000.0))
    except (TypeError, ValueError, OSError):
        return str(value or "")


def _chip(text: str, color: str = "#334155") -> str:
    return (
        f"<span style='background:{color}1a;color:{color};border:1px solid {color}55;"
        f"border-radius:999px;padding:1px 8px;font-size:0.75rem;margin-right:4px;"
        f"white-space:nowrap'>{text}</span>"
    )


def _strength_bar(value: float, label: str = "记忆强度") -> None:
    st.progress(max(0.0, min(1.0, float(value or 0.0))), text=f"{label} {float(value or 0.0):.0%}")


# ---------------------------------------------------------------------------
# 概览
# ---------------------------------------------------------------------------


def render_stats(snap: dict[str, Any]) -> None:
    """三层条数 / 未闭环 / 已淡化 四个指标。"""
    stats = snap.get("stats") or {}
    cols = st.columns(4)
    cols[0].metric("短期 · 轮次", stats.get("turns", 0), help="逐字保留的最近对话")
    cols[1].metric("中期 · 情景", stats.get("episodes", 0), help="固化出的 Episode 数")
    cols[2].metric(
        "长期 · 事实",
        f"{stats.get('facts', 0)}+{stats.get('shared_facts', 0)}",
        help="私有事实 + 跨角色共享事实",
    )
    cols[3].metric("未闭环", stats.get("open_loops", 0), help="下次要主动跟进的事")
    st.caption(
        f"已淡化（不再注入但保留审计）：{stats.get('archived', 0)} 条 · "
        f"平均记忆强度：{float(stats.get('avg_strength', 0.0) or 0.0):.0%}"
    )


def render_short(snap: dict[str, Any]) -> None:
    """短期工作记忆：最近逐字对话 + 滚动摘要。"""
    short = snap.get("short") or {}
    st.markdown(f"**{cfg.TIER_LABELS['short']}** · {short.get('count', 0)} 条")
    summary = (short.get("summary") or "").strip()
    if summary:
        st.info(f"滚动摘要：{summary}")
    turns = short.get("turns") or []
    if not turns:
        st.caption("还没有对话。")
    for turn in reversed(turns):
        who = "你" if turn.get("role") == "user" else "TA"
        stamp = turn.get("ts_label") or ""
        emotion = turn.get("emotion")
        tag = _chip(f"{emotion}", cfg.EMOTION_COLOR.get(emotion, "#64748B")) if emotion else ""
        st.markdown(
            f"{tag}**{who}** · {stamp}\n\n{turn.get('content', '')}",
            unsafe_allow_html=True,
        )
    st.divider()


def render_mid(snap: dict[str, Any], role_name: Optional[str] = None) -> None:
    """中期情景记忆：Episode 卡片。"""
    items = (snap.get("mid") or {}).get("items") or []
    st.markdown(f"**{cfg.TIER_LABELS['mid']}** · {len(items)} 条")
    if not items:
        st.caption("还没固化出情景记忆——聊满 4 轮会自动「睡眠整理」一次。")
    for item in items:
        archived = bool(item.get("archived"))
        title = item.get("title") or "（未命名片段）"
        prefix = "🗄 " if archived else ""
        with st.expander(f"{prefix}{title}　·　强度 {float(item.get('strength', 0)):.0%}"):
            st.write(item.get("summary") or "")
            topics = item.get("topics") or []
            if topics:
                st.markdown("".join(_chip(t, _TIER_COLOR["mid"]) for t in topics), unsafe_allow_html=True)
            st.caption(
                f"效价 {float(item.get('valence', 0)):+.2f} · 唤醒 {float(item.get('arousal', 0)):.2f} · "
                f"重要性 {float(item.get('importance', 0)):.2f} · 召回 {item.get('recall_count', 0)} 次 · "
                f"{item.get('created_label', '')}"
            )
            _strength_bar(item.get("strength", 0.0))
            if role_name and st.button("🗑 让 TA 忘掉这段", key=f"forget_mid_{item.get('id')}"):
                state.forget_memory(role_name, str(item.get("id")), tier="mid")
                st.rerun()
    st.divider()


def render_long(snap: dict[str, Any], role_name: Optional[str] = None) -> None:
    """长期语义画像：Fact 列表（含冲突消解痕迹与共享事实）。"""
    long = snap.get("long") or {}
    items = long.get("items") or []
    shared = long.get("shared") or []
    st.markdown(f"**{cfg.TIER_LABELS['long']}** · {len(items)} 私有 / {len(shared)} 共享")
    st.caption(
        "同一件事被反复提到时不会堆成多条：证据数 +1、置信度 +0.1，排序也随之靠前"
        "（置信度 × 类别权重，禁忌/约定额外 +0.12）。"
    )
    if not items and not shared:
        st.caption("还没有沉淀出稳定事实。多说点关于你自己的事，TA 会记住。")

    def _row(fact: dict[str, Any], tier: str = "long") -> None:
        archived = bool(fact.get("archived"))
        superseded = fact.get("superseded_by")
        color = "#94A3B8" if (archived or superseded) else "#0F172A"
        category = fact.get("category") or "近况"
        tone = "🗄 " if archived else ("♻️ " if superseded else "")
        st.markdown(
            f"{_chip(category, _TIER_COLOR['long'])}"
            f"<span style='color:{color}'>{tone}{fact.get('content', '')}</span>",
            unsafe_allow_html=True,
        )
        extra = (
            f"置信 {float(fact.get('confidence', 0)):.0%} · 证据 {fact.get('evidence_count', 1)} 条 · "
            f"强度 {float(fact.get('strength', 0)):.0%} · 最近确认 {fact.get('last_confirmed_label', '')}"
        )
        if superseded:
            extra += f" · 已被新事实取代（审计保留：{superseded}）"
        cols = st.columns([4, 1, 1])
        cols[0].caption(extra)
        if role_name and cols[1].button("✏️ 改正", key=f"correct_{tier}_{fact.get('id')}"):
            correct_dialog(role_name, fact)
        if role_name and cols[2].button("🗑 忘掉", key=f"forget_{tier}_{fact.get('id')}"):
            state.forget_memory(role_name, str(fact.get("id")), tier=tier)
            st.rerun()

    for fact in items:
        _row(fact, "long")
    if shared:
        st.markdown("**跨角色共享的客观事实**")
        for fact in shared:
            _row(fact, "shared")
    st.divider()


def render_loops(snap: dict[str, Any], engine: MemoryEngine, role_name: str) -> None:
    """未闭环事件：可一键标记完成。"""
    loops = snap.get("loops") or []
    st.markdown(f"**未闭环事件** · {len(loops)} 条")
    if not loops:
        st.caption("没有待跟进的事。TA 会从对话里自己找（面试 / 体检 / 约定…）。")
        return
    for loop in loops:
        cols = st.columns([5, 1])
        due = f"（{loop.get('due_hint')}）" if loop.get("due_hint") else ""
        cols[0].markdown(
            f"{_chip(loop.get('kind', '待办'), '#0EA5E9')}"
            f"<span style='color:#0F172A'>{loop.get('what', '')}{due}</span>",
            unsafe_allow_html=True,
        )
        if cols[1].button("已完成", key=f"close_{loop.get('id')}"):
            engine.close_loop(role_name, loop.get("id"))
            st.rerun()


def render_relationship(snap: dict[str, Any]) -> None:
    """关系档案：亲密度 / 阶段 / 称呼演化 / 共同梗。"""
    rel = snap.get("relationship") or {}
    intimacy = float(rel.get("intimacy", 0) or 0)
    stage = rel.get("stage") or cfg.stage_of(intimacy)
    st.markdown(f"**关系阶段** {_chip(stage, '#6366F1')}", unsafe_allow_html=True)
    st.progress(min(1.0, intimacy / 100.0), text=f"亲密度 {intimacy:.1f} / 100")
    st.progress(min(1.0, float(rel.get("trust", 0) or 0) / 100.0), text=f"信任 {float(rel.get('trust', 0) or 0):.1f} / 100")
    nickname = rel.get("nickname") or "（还没给你起称呼）"
    st.caption(f"TA 现在叫你：**{nickname}** · 累计 {rel.get('turn_count', 0)} 轮")
    history = rel.get("nickname_history") or []
    if len(history) > 1:
        trail = " → ".join(str(h.get("value")) for h in history[-5:] if isinstance(h, dict))
        st.caption(f"称呼演化：{trail}")
    jokes = rel.get("inside_jokes") or []
    themes = rel.get("shared_themes") or []
    if jokes:
        st.markdown("".join(_chip(f"梗：{j}", "#EC4899") for j in jokes), unsafe_allow_html=True)
    if themes:
        st.markdown("".join(_chip(f"常聊：{t}", "#0EA5E9") for t in themes), unsafe_allow_html=True)
    st.caption(
        "这两条不是给用户看的分数，是内部状态：亲密度决定关系阶段（20/45/75 三档），"
        "阶段再去调语气——称呼、能不能开玩笑、多久回一次；信任只做一件事："
        "它越高，难过时越敢碰你的旧事（情绪门控门槛最多放宽 30%）。"
        "所以放在记忆舱里，不占聊天界面。"
    )


def render_decay_curve(snap: dict[str, Any]) -> None:
    """遗忘曲线：未来 14 天的强度预测。"""
    curve = snap.get("decay_curve") or []
    peak = max(
        [float(point.get("episode") or 0) for point in curve]
        + [float(point.get("fact") or 0) for point in curve]
        + [0.0]
    )
    if not curve or peak <= 0:
        st.caption("还没有可预测的记忆——聊满 4 轮固化一次之后，这里会画出未来 14 天的强度衰减。")
        return
    frame = pd.DataFrame(curve)
    frame = frame.rename(columns={"label": "日期", "episode": "中期情景", "fact": "长期事实"})
    # 按原始顺序锁定 x 轴（否则 "+10天" 会被排到 "+2天" 前面）
    frame["日期"] = pd.Categorical(frame["日期"], categories=list(frame["日期"]), ordered=True)
    st.line_chart(frame.set_index("日期"), height=180)
    st.caption("不主动召回时，记忆强度按指数衰减；每次被召回都会强化（间隔重复）。")


# ---------------------------------------------------------------------------
# 五层记忆：L0 原文库 / L3 固化快照 / L4 自我认知
# ---------------------------------------------------------------------------

_LAYER_ROWS = (
    ("L0", "原文库", "archive", "全量原文，永不删除，可检索"),
    ("L1", "会话层", "short", "最近 20 轮 + 滚动摘要 + 每 4 轮固化的情景"),
    ("L2", "长期事实", "long", "置信度 / 证据数 / 类别权重 / 衰减 / 归档"),
    ("L3", "固化快照", "snapshots", "每次固化的产物落盘留档，可回溯"),
    ("L4", "自我认知", "self", "它对自己的叙述，只在新会话首轮注入"),
)


def render_layer_overview(snap: dict[str, Any]) -> None:
    """五层记忆总览：每层现在有多少、注入策略是什么（一屏看懂）。"""
    stats = snap.get("stats") or {}
    counts = {
        "archive": int((snap.get("archive") or {}).get("messages", 0) or 0),
        "short": int(stats.get("turns", 0) or 0),
        "long": int(stats.get("facts", 0) or 0) + int(stats.get("shared_facts", 0) or 0),
        "snapshots": int((snap.get("snapshots") or {}).get("count", 0) or 0),
        "self": int((snap.get("self") or {}).get("count", 0) or 0),
    }
    rows = [
        {
            "层": layer,
            "名字": name,
            "现在有多少": counts.get(key, 0),
            "生命周期与注入策略": policy,
        }
        for layer, name, key, policy in _LAYER_ROWS
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("前三层记的都是「你」，第四层记的是「它」——前者是资料，后者才是性格的连续性。")
    st.caption(
        f"中期情景 {stats.get('episodes', 0)} 条 · 未闭环 {stats.get('open_loops', 0)} 条 · "
        f"已淡化 {stats.get('archived', 0)} 条 · 平均强度 {float(stats.get('avg_strength', 0.0) or 0.0):.0%}"
    )


def render_archive(engine: MemoryEngine, role_name: str, snap: dict[str, Any]) -> None:
    """L0 原文库：全量对话检索（回答「你到底是在哪句话里记住我的」）。"""
    archive = snap.get("archive") or {}
    messages = int(archive.get("messages", 0) or 0)
    first = archive.get("first_label") or "—"
    last = archive.get("last_label") or "—"
    st.markdown(f"**L0 原文库** · {messages} 条原文（含你的 {int(archive.get('user_messages', 0) or 0)} 条）")
    st.caption(f"最早 {first} · 最近 {last} · 永不删除、永不改写，是记忆的底稿")

    query = st.text_input(
        "在原文库里搜一句话",
        key="archive_query",
        placeholder="试试：面试 / 加班 / 我妈 / 下雨",
    )
    if query:
        hits = state.search_archive(role_name, query, limit=cfg.ARCHIVE_TOP_K)
        if not hits:
            st.info("没找到相关原文——原文库只存「说过的话」，不猜。")
        else:
            st.caption(f"命中 {len(hits)} 条（按分句最大相似度排序）")
            for hit in hits:
                who = "你" if hit.get("sender") == "user" else "TA"
                st.markdown(
                    f"{_chip(who, '#0EA5E9' if who == '你' else '#6366F1')}"
                    f"<span style='color:#475569'>{hit.get('ts_label') or ''}　·　"
                    f"相似度 {float(hit.get('score', 0)):.2f}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"> {hit.get('text', '')}")
        st.divider()

    recent = state.role_thread(role_name)[-20:]
    if recent:
        st.markdown("**最近 20 条原文**")
        for message in reversed(recent):
            who = "你" if message.get("role") == "user" else "TA"
            stamp = fmt_ts(message.get("ts"))
            st.markdown(
                f"{_chip(who, '#0EA5E9' if who == '你' else '#6366F1')}"
                f"<span style='color:#475569'>{stamp}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"> {str(message.get('content') or '')[:160]}")
    else:
        st.caption("原文库还是空的——先聊两句。")


def render_snapshots(snap: dict[str, Any]) -> None:
    """L3 固化快照：每次「睡眠整理」的产物留档，可回溯。"""
    items = (snap.get("snapshots") or {}).get("items") or []
    st.markdown(f"**L3 固化快照** · {len(items)} 份")
    if not items:
        st.caption("每 4 轮会做一次「睡眠整理」，每次的产物都会落盘成一份快照。")
        return
    for item in items:
        label = item.get("label") or ""
        title = item.get("episode_title") or "（未命名片段）"
        with st.expander(f"🗂 {label} · {title}"):
            st.caption(
                f"本次固化：新增 {item.get('facts_added', 0)} 条事实 · "
                f"更新 {item.get('facts_updated', 0)} 条事实"
            )
            note = item.get("self_note")
            if note:
                st.markdown(f"🪞 同时写下一条自我叙述：{note}")
            st.caption("快照文件保存在 data/archive/ 下，重装软件也不会丢。")


def render_self_notes(snap: dict[str, Any]) -> None:
    """L4 自我认知：它对自己的叙述（只在新会话首轮注入）。"""
    items = (snap.get("self") or {}).get("items") or []
    st.markdown(f"**L4 自我认知** · {len(items)} 条")
    st.caption("这一层记的不是你，是「它」。只在新会话首轮注入一次——每轮都灌就变成洗脑了。")
    if not items:
        st.caption("还没有自我叙述。固化几轮之后，它会开始对自己有说法。")
        return
    for item in items:
        note_type = item.get("note_type") or "成长"
        color = {"关系": "#EC4899", "情绪": "#0EA5E9", "表达": "#A78BFA", "成长": "#F59E0B"}.get(note_type, "#64748B")
        st.markdown(
            f"{_chip(note_type, color)}<span style='color:#0F172A'>{item.get('content', '')}</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"首次 {item.get('created_label', '')} · 被说起 {item.get('mention_count', 1)} 次")


@st.dialog("✏️ 纠正这条记忆")
def correct_dialog(role_name: str, fact: dict[str, Any]) -> None:
    """用户亲手改一条事实（记忆可纠正）。"""
    st.caption("改完会新增一条高置信事实，旧的那条标成「已被取代」留在审计记录里——它以前记错过什么，看得见。")
    content = st.text_area("TA 应该记住的是……", value=str(fact.get("content") or ""), height=90)
    categories = list(cfg.CATEGORIES)
    current = str(fact.get("category") or "近况")
    index = categories.index(current) if current in categories else len(categories) - 1
    category = st.selectbox("分类", categories, index=index)
    if st.button("保存纠正", type="primary", use_container_width=True):
        if state.correct_fact(role_name, str(fact.get("id") or ""), content, category=category):
            state.flash("已按你的说法改正这条记忆")
            st.rerun()
        else:
            st.error("这条记忆已经不在了，刷新看看。")


# ---------------------------------------------------------------------------
# 可解释 Trace
# ---------------------------------------------------------------------------


def render_trace(last_turn: Optional[dict[str, Any]]) -> None:
    """上一轮检索的 Trace：注入了什么、为什么、没注入什么。"""
    if not last_turn:
        st.caption("先聊一句，这里会显示本轮记忆检索的全部依据。")
        return
    bundle = last_turn.get("bundle")
    trace = getattr(bundle, "trace", None)
    if trace is None:
        st.caption("本轮没有产生 Trace。")
        return

    items = list(getattr(trace, "items", []) or [])
    skipped = list(getattr(trace, "skipped", []) or [])
    notes = list(getattr(trace, "notes", []) or [])
    block = last_turn.get("memory_block") or ""

    st.caption(
        f"注入 {len(items)} 条 · 抑制 {len(skipped)} 条 · 记忆块 {len(block)} 字 / 预算 {cfg.MEMORY_BLOCK_BUDGET}"
    )
    if notes:
        for note in notes:
            st.warning(note, icon="🧠")

    if items:
        rows = [
            {
                "层": _TIER_ALIAS.get(item.tier, item.tier),
                "类型": item.kind,
                "内容": (item.text or "")[:60],
                "分数": round(float(item.score), 3),
                "相似": round(float((item.components or {}).get("sim", 0.0)), 2),
                "重要": round(float((item.components or {}).get("importance", 0.0)), 2),
                "新鲜": round(float((item.components or {}).get("recency", 0.0)), 2),
                "情绪": round(float((item.components or {}).get("emotion", 0.0)), 2),
                "必注入": "是" if item.pinned else "",
                "理由": item.reason,
            }
            for item in items
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(420, 42 + 35 * len(rows)))

    if skipped:
        st.markdown("**本轮没有采用的记忆**")
        for item in skipped:
            st.markdown(
                f"{_chip(_TIER_ALIAS.get(item.tier, item.tier), _TIER_COLOR.get(item.tier, '#64748B'))}"
                f"{(item.text or '')[:60]}　·　理由：{item.reason}",
                unsafe_allow_html=True,
            )

    with st.expander("本轮注入给模型的记忆块原文"):
        st.code(block or "（空）", language="text")
    with st.expander("本轮完整 System Prompt"):
        st.code(last_turn.get("system_prompt") or "", language="text")


# ---------------------------------------------------------------------------
# 消融实验
# ---------------------------------------------------------------------------


def render_ablation(engine: MemoryEngine, role_name: str) -> None:
    """消融开关 + 一键固化。"""
    st.caption("现场对比：关掉某一层记忆，观察回复差异。")
    st.checkbox("中期记忆（情景 Episode）", key="ab_mid")
    st.checkbox("长期记忆（语义事实 Fact）", key="ab_long")
    st.checkbox("情绪门控（防翻旧账）", key="ab_mood_gate")
    st.checkbox("记忆冲突消解（superseded_by）", key="ab_conflict")
    st.checkbox("自动固化（每 4 轮睡眠整理）", key="ab_auto_consolidate")

    pending = 0
    try:
        pending = int(engine.pending_turns(role_name))
    except Exception:  # 老版本引擎没有该方法时不影响主流程
        pending = 0
    st.divider()
    st.caption(f"待固化对话：{pending} 条 turn")
    if st.button("立即固化一轮记忆", use_container_width=True, type="primary"):
        result = engine.consolidate(role_name, force=True)
        notes = getattr(result, "notes", []) or []
        if getattr(result, "done", False):
            st.success("；".join(notes) or "固化完成")
        else:
            st.info("；".join(notes) or "没有新的对话需要固化")
