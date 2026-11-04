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
        st.markdown(f"{tag}**{who}** · {stamp}\n\n{turn.get('content', '')}")
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
        cols = st.columns([5, 1])
        cols[0].caption(extra)
        if role_name and cols[1].button("🗑 忘掉", key=f"forget_{tier}_{fact.get('id')}"):
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
        cols[0].markdown(f"{_chip(loop.get('kind', '待办'), '#0EA5E9')}{loop.get('what', '')}{due}")
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


def render_decay_curve(snap: dict[str, Any]) -> None:
    """遗忘曲线：未来 14 天的强度预测。"""
    curve = snap.get("decay_curve") or []
    if not curve:
        st.caption("还没有足够记忆来画遗忘曲线。")
        return
    frame = pd.DataFrame(curve)
    frame = frame.rename(columns={"label": "日期", "episode": "中期情景", "fact": "长期事实"})
    st.line_chart(frame.set_index("日期"), height=180)
    st.caption("不主动召回时，记忆强度按指数衰减；每次被召回都会强化（间隔重复）。")


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
    budget = getattr(trace, "budget", 0)
    block = last_turn.get("memory_block") or ""

    st.caption(
        f"注入 {len(items)} 条 · 抑制 {len(skipped)} 条 · 记忆块 {budget or len(block)} 字 / 预算 {cfg.MEMORY_BLOCK_BUDGET}"
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
