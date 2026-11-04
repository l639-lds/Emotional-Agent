"""人设锻造炉：把一句描述编译成结构化人设卡的人机界面。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from core import config as cfg
from persona_forge import compile_persona, interview
from persona_forge.schema import Critique, HealthReport, PersonaCard
from ui import chat, state

_DIM_ORDER = ("可执行性", "一致性", "差异化", "情感深度", "安全性", "可演示性")


def _get(obj: Any, key: str, default: Any = "") -> Any:
    """同时兼容 dataclass 与 dict 的取值。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _kv(label: str, value: Any) -> None:
    text = str(value or "").strip()
    if text:
        st.markdown(f"**{label}**　{text}")


def render_card_preview(card: PersonaCard) -> None:
    st.markdown(f"### {_get(card, 'avatar') or '🎭'} {_get(card, 'name') or '未命名角色'}")
    tagline = _get(card, "tagline")
    if tagline:
        st.caption(tagline)

    identity = _get(card, "identity", {}) or {}
    if isinstance(identity, dict) and identity:
        st.markdown(
            " ".join(
                f"`{key}` {val}" for key, val in identity.items() if isinstance(val, str) and val.strip()
            )
        )

    _kv("背景", _get(card, "backstory"))
    values = _as_list(_get(card, "values"))
    if values:
        _kv("价值排序", " · ".join(values))

    speech = _get(card, "speech", None)
    if speech is not None:
        st.markdown("**语音指纹**")
        st.caption(
            f"语域：{_get(speech, 'register')}　·　平均句长：{_get(speech, 'avg_sentence_len')} 字　·　"
            f"标点习惯：{_get(speech, 'punctuation')}"
        )
        tics = _as_list(_get(speech, "tics"))
        if tics:
            _kv("口癖", " / ".join(tics))
        banned = _as_list(_get(speech, "banned_words"))
        if banned:
            _kv("禁语", " / ".join(banned))
        for line in _as_list(_get(speech, "sample_lines"))[:3]:
            st.markdown(f"> {line}")

    rules = _get(card, "emotion_map", []) or []
    if rules:
        st.markdown("**情绪反应表**")
        for rule in rules[:5]:
            trigger = _get(rule, "trigger")
            st.caption(
                f"触发：{trigger} → 反应：{_get(rule, 'reaction')}　（身体语言：{_get(rule, 'body_language')}）"
            )

    arc = _get(card, "relationship_arc", {}) or {}
    if isinstance(arc, dict) and arc:
        st.markdown("**关系弧（人格漂移）**")
        for stage in cfg.CANONICAL_STAGES:
            if stage in arc:
                st.caption(f"{stage}：{arc[stage]}")

    for label, key in (
        ("边界", "boundaries"),
        ("禁忌", "taboos"),
        ("反套路", "anti_patterns"),
        ("记忆钩子", "memory_hooks"),
    ):
        items = _as_list(_get(card, key))
        if items:
            _kv(label, " · ".join(items))

    if _get(card, "legacy", False):
        st.info("这是从旧版纯文本人设包装而来的卡片（legacy）。重新锻造可升级为结构化卡。")


def _critique_block(critique: Critique) -> None:
    scores = _get(critique, "scores", {}) or {}
    st.markdown("**自评 6 维**")
    cols = st.columns(3)
    for index, dim in enumerate(_DIM_ORDER):
        value = int(scores.get(dim, 0) or 0)
        cols[index % 3].progress(min(1.0, max(0.0, value / 100.0)), text=f"{dim} {value}")
    verdict = _get(critique, "verdict")
    if verdict:
        st.caption(f"评语：{verdict}")
    issues = _as_list(_get(critique, "issues"))
    if issues:
        st.markdown("**被点名的问题（已定向重写）**")
        for issue in issues:
            st.caption(f"· {issue}")


def _health_block(health: HealthReport) -> None:
    checks = _get(health, "checks", []) or []
    if not checks:
        return
    st.markdown(f"**人设体检** · {_get(health, 'score', 0)} 分")
    for check in checks:
        ok = bool(_get(check, "ok", False))
        st.caption(f"{'✅' if ok else '⚠️'} {_get(check, 'name')}：{_get(check, 'detail')}")
    summary = _get(health, "summary")
    if summary:
        st.caption(summary)


@st.dialog("⚒ 人设锻造炉", width="large")
def forge_dialog() -> None:
    """一句话描述 → 结构化人设卡 → 自评 → 定向重写 → 3 问体检。"""
    st.caption("描述越具体越好：TA 是谁、在哪里、怎么说话、在意什么、绝不会做什么。")

    description = st.text_area(
        "想要一个什么样的角色？",
        height=110,
        placeholder="例：在城中村开馄饨摊的退休刑警，说话短促爱打岔，见不得人受欺负，"
        "嘴上骂骂咧咧心里软，一提当年案子就沉默。",
    )
    knob_cols = st.columns(4)
    knobs: dict[str, float] = {}
    for index, knob in enumerate(cfg.PERSONA_KNOBS):
        knobs[knob] = knob_cols[index].slider(knob, 0.0, 1.0, 0.5, 0.1)

    if st.button("开始编译", type="primary", use_container_width=True, disabled=not description.strip()):
        with st.spinner("正在生成 → 自评 → 重写 → 体检……"):
            try:
                st.session_state["forge_result"] = compile_persona(description.strip(), knobs=knobs)
            except Exception as exc:  # 兜底：编译器内部已降级，这里只防意外
                st.error(f"编译失败：{exc}")
                return

    result = st.session_state.get("forge_result")
    if result is None:
        return

    card = _get(result, "card", None)
    if card is None:
        st.error("编译器没有返回人设卡。")
        return

    st.divider()
    tabs = st.tabs(["人设卡", "自评与体检", "编译轨迹"])
    with tabs[0]:
        render_card_preview(card)
    with tabs[1]:
        _critique_block(_get(result, "critique", {}) or {})
        _health_block(_get(result, "health", {}) or {})
    with tabs[2]:
        for step in _as_list(_get(result, "steps", [])):
            st.caption(f"· {step}")
        st.caption(
            f"重写轮数：{_get(result, 'repair_rounds', 0)} · "
            f"模型调用：{'真实 LLM' if _get(result, 'used_llm', False) else '离线降级'}"
        )

    st.divider()
    default_name = _get(card, "name") or "新角色"
    name = st.text_input("保存为角色名", value=str(default_name)[:20])
    avatar = st.text_input("头像 emoji", value=str(_get(card, "avatar") or "🎭")[:4])
    raw_scores = _get(_get(result, "critique", None) or {}, "scores", {}) or {}
    values = [int(v or 0) for v in raw_scores.values()] if isinstance(raw_scores, dict) else []
    avg_score = int(sum(values) / len(values)) if values else None
    cols = st.columns(2)
    if cols[0].button("保存并开始聊天", type="primary", use_container_width=True, disabled=not name.strip()):
        card.name = name.strip()
        card.avatar = avatar.strip() or "🎭"
        state.save_forged_role(name.strip(), card, knobs=knobs, score=avg_score)
        st.session_state["role"] = name.strip()
        st.session_state["forge_result"] = None
        state.flash(f"已保存角色「{name.strip()}」，可以开始聊天了")
        st.rerun()
    if cols[1].button("丢弃结果", use_container_width=True):
        st.session_state["forge_result"] = None
        st.rerun()


def render_coach_panel() -> None:
    """人格教练整页（侧边栏第四个视图）：五轮访谈 → 人设速写 → 同一条编译流水线。"""
    st.markdown("#### 🧭 人格教练")
    st.caption("想不清「我想要一个什么样的角色」的时候走这条：聊五轮，教练把回答收束成人设速写，再编译。")
    coach = st.session_state.get("coach")
    if not isinstance(coach, dict) or "index" not in coach:
        coach = interview.new_state()
        st.session_state["coach"] = coach

    st.caption("答得越具体越好：教练会顺着你上一句继续问，最后把回答收束成一段人设速写再编译。")
    answered = len([v for v in (coach.get("answers") or {}).values() if str(v).strip()])
    done = bool(coach.get("done"))
    st.progress(
        1.0 if done else min(1.0, answered / len(interview.STAGES)),
        text=f"{interview.progress_text(coach)} · 已答 {answered} 问"
        + ("（已收束）" if done else ""),
    )

    if not done and not str(coach.get("question") or "").strip():
        with st.spinner("教练在想下一个问题……"):
            interview.ask_question(coach)

    turns = [
        {"role": str(t.get("role") or "assistant"), "content": str(t.get("content") or "")}
        for t in (coach.get("turns") or [])
    ]
    if turns:
        chat.render_thread(
            turns,
            role_name=interview.COACH_NAME,
            role_avatar=interview.COACH_AVATAR,
            user_avatar=state.user_avatar(),
        )

    if not done:
        stage = interview.stage_at(int(coach.get("index") or 0)) or {}
        st.caption(f"💡 {stage.get('hint', '')}")
        answer = st.text_area(
            "你的回答",
            key=f"coach_answer_{coach.get('index')}_{len(turns)}",
            height=90,
            placeholder=str(stage.get("placeholder", "")),
        )
        col_ok, col_skip, col_reset = st.columns([2, 1, 1])
        if col_ok.button(
            "回答这一问", type="primary", use_container_width=True, disabled=not answer.strip()
        ):
            interview.take_answer(coach, answer)
            st.session_state["coach"] = coach
            st.rerun()
        if col_skip.button("跳过", use_container_width=True):
            interview.skip_stage(coach)
            st.session_state["coach"] = coach
            st.rerun()
        if col_reset.button("重新开始", use_container_width=True):
            st.session_state["coach"] = interview.new_state()
            st.session_state["coach_result"] = None
            st.rerun()
        return

    result = st.session_state.get("coach_result")
    if result is None:
        with st.spinner("教练在整理人设速写，然后编译 → 自评 → 体检……"):
            composed = interview.compose(coach)
            knobs = {
                knob: float((composed.get("knobs") or {}).get(knob, 0.5)) for knob in cfg.PERSONA_KNOBS
            }
            coach["knobs_used"] = knobs
            st.session_state["coach"] = coach
            st.session_state["coach_brief"] = composed["brief"]
            try:
                result = compile_persona(composed["brief"], knobs=knobs)
            except Exception as exc:  # 编译器内部已降级，这里只防意外
                st.error(f"编译失败：{exc}")
                return
            st.session_state["coach_result"] = result

    card = _get(result, "card", None)
    if card is None:
        st.error("编译器没有返回人设卡。")
        return

    brief = str(st.session_state.get("coach_brief") or coach.get("brief") or "")
    with st.expander("访谈收束成的人设速写（编译器的输入）", expanded=False):
        st.markdown(brief or "（空）")

    st.divider()
    tabs = st.tabs(["人设卡", "自评与体检", "编译轨迹"])
    with tabs[0]:
        render_card_preview(card)
    with tabs[1]:
        _critique_block(_get(result, "critique", {}) or {})
        _health_block(_get(result, "health", {}) or {})
    with tabs[2]:
        for step in _as_list(_get(result, "steps", [])):
            st.caption(f"· {step}")

    st.divider()
    default_name = str(_get(card, "name") or coach.get("name_hint") or "新角色")[:20]
    name = st.text_input("保存为角色名", value=default_name, key="coach_save_name")
    avatar = st.text_input(
        "头像 emoji", value=str(_get(card, "avatar") or "🎭")[:4], key="coach_save_avatar"
    )
    raw_scores = _get(_get(result, "critique", None) or {}, "scores", {}) or {}
    values = [int(v or 0) for v in raw_scores.values()] if isinstance(raw_scores, dict) else []
    avg_score = int(sum(values) / len(values)) if values else None
    knobs = coach.get("knobs_used") or {}
    cols = st.columns(3)
    if cols[0].button("保存并开始聊天", type="primary", use_container_width=True, disabled=not name.strip()):
        card.name = name.strip()
        card.avatar = avatar.strip() or "🎭"
        state.save_forged_role(name.strip(), card, knobs=knobs, score=avg_score)
        st.session_state["role"] = name.strip()
        st.session_state["coach"] = interview.new_state()
        st.session_state["coach_result"] = None
        state.flash(f"已保存角色「{name.strip()}」，可以开始聊天了")
        st.session_state["pending_view"] = "💬 对话"
        st.rerun()
    if cols[1].button("重新聊一次", use_container_width=True):
        st.session_state["coach"] = interview.new_state()
        st.session_state["coach_result"] = None
        st.rerun()
    if cols[2].button("关掉", use_container_width=True):
        st.session_state["coach_result"] = None
        st.rerun()

