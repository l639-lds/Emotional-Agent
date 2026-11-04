"""EmoMem · 情感陪伴 Agent（Streamlit 入口）。

本文件只负责界面编排：侧边栏（角色 / 人设 / 运行模式）、对话区、右侧记忆舱。
业务逻辑分别在 `memory_os`（三层记忆）、`persona_forge`（人设编译器）、
`core`（配置 / LLM / 离线演示）与 `ui`（状态与面板渲染）中。
"""

from __future__ import annotations

import streamlit as st

from core import config as cfg
from core import llm
from ui import forge, panels, state

USER_AVATAR = "🙂"

st.set_page_config(
    page_title="EmoMem · 情感陪伴",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

state.init_session()

role_file = state.load_role_file()
names = state.role_names(role_file)
if st.session_state.get("role") not in names:
    st.session_state["role"] = names[0]
role_name = st.session_state["role"]
card = state.get_card(role_name, role_file)
state.ensure_opening(role_name, card)

engine = state.build_engine()
if st.session_state.get("decay_role") != role_name:
    try:
        engine.decay_pass(role_name)
    except Exception:  # 遗忘曲线只是加分项，失败不影响对话
        pass
    st.session_state["decay_role"] = role_name

snapshot = engine.snapshot_for_ui(role_name)
relationship = snapshot.get("relationship") or {}
intimacy = float(relationship.get("intimacy", 0) or 0)
stage = relationship.get("stage") or cfg.stage_of(intimacy)
avatar = state.avatar_for(role_name, card, role_file)


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🧠 EmoMem")
    st.caption("情感感知的三层记忆 · 人设编译器")
    if llm.is_demo_mode():
        st.warning(state.api_key_hint(), icon="🎬")
    else:
        st.success(state.api_key_hint(), icon="🔌")

    with st.expander("运行模式 / API Key", expanded=llm.is_demo_mode()):
        st.caption("留空则在离线演示模式下运行：本地假模型会把三层记忆、固化、人设编译全链路跑通。")
        key_input = st.text_input("DeepSeek API Key", type="password", placeholder="sk-...")
        if st.button("应用 Key", use_container_width=True):
            state.set_api_key(key_input)
            st.rerun()

    st.divider()
    picked = st.selectbox("当前角色", names, index=names.index(role_name))
    if picked != role_name:
        st.session_state["role"] = picked
        st.session_state["last_turn"] = None
        st.rerun()

    if st.button("⚒ 锻造新角色", use_container_width=True, type="primary"):
        forge.forge_dialog()

    with st.expander("人设卡（TA 是谁）", expanded=False):
        forge.render_card_preview(card)

    with st.expander("记忆维护", expanded=False):
        st.caption(f"当前引擎：{llm.mode_label()}")
        if st.button("清空当前角色记忆", use_container_width=True):
            state.reset_role_memory(role_name)
            st.session_state["last_turn"] = None
            st.session_state["retry"] = None
            state.flash(f"已清空「{role_name}」的记忆与对话")
            st.rerun()
        if st.button("清空全部角色记忆", use_container_width=True):
            state.reset_everything()
            st.session_state["last_turn"] = None
            st.session_state["retry"] = None
            state.flash("已清空全部角色记忆")
            st.rerun()
        custom = [n for n in names if n not in state.DEFAULT_ROLE_RAW]
        if custom:
            victim = st.selectbox("删除自定义角色", custom, index=0)
            if st.button("删除该角色", use_container_width=True):
                state.delete_role(victim)
                if victim == role_name:
                    st.session_state["role"] = names[0]
                st.rerun()


# ---------------------------------------------------------------------------
# 主区标题
# ---------------------------------------------------------------------------
head_left, head_right = st.columns([3, 2])
with head_left:
    st.markdown(f"### {avatar} {role_name}")
    st.caption(f"关系阶段 **{stage}** · 亲密度 {intimacy:.0f}/100 · 调用模型 {cfg.DEEPSEEK_MODEL}")
with head_right:
    loop_count = (snapshot.get("stats") or {}).get("open_loops", 0)
    st.caption(
        f"短期 {len((snapshot.get('short') or {}).get('turns') or [])} 条 · "
        f"中期 {len((snapshot.get('mid') or {}).get('items') or [])} 条 · "
        f"长期 {len((snapshot.get('long') or {}).get('items') or [])} 条 · 待跟进 {loop_count} 件"
    )

for message in state.take_flash():
    st.toast(message, icon="🧠")

chat_col, mem_col = st.columns([1.6, 1], gap="large")


# ---------------------------------------------------------------------------
# 对话区
# ---------------------------------------------------------------------------
with chat_col:
    thread = state.role_thread(role_name)
    for message in thread:
        sender = message.get("role", "assistant")
        with st.chat_message(sender, avatar=(USER_AVATAR if sender == "user" else avatar)):
            st.markdown(message.get("content", ""))
            emotion = message.get("emotion")
            if emotion:
                st.caption(f"{cfg.EMOTION_EMOJI.get(emotion, '')} 你当时的情绪：{emotion}")
            if message.get("initiative"):
                st.caption(f"↪ TA 追问：{message['initiative']}")
            if message.get("loop_hint"):
                st.caption(f"📌 记下待办：{message['loop_hint']}")
            if message.get("memory_update"):
                st.caption(f"🧠 本轮记忆更新：{message['memory_update']}")

    retry = st.session_state.get("retry")
    if retry:
        st.error(f"这一轮失败了：{retry.get('error')}")
        if st.button("重试这一句", type="primary"):
            try:
                st.session_state["last_turn"] = state.chat_once(role_name, retry["text"])
                st.session_state["retry"] = None
            except llm.LLMError as exc:
                st.session_state["retry"] = {"text": retry["text"], "error": str(exc)}
            st.rerun()

    openers: list[str] = []
    try:
        openers = engine.proactive_openers(role_name, limit=1)
    except Exception:
        openers = []
    # 只有真的"有件事没闭环"时才让 TA 主动开口，避免把兜底开场白当成关怀
    if openers and snapshot.get("loops"):
        with st.container(border=True):
            st.caption("TA 想起还没问你的事：")
            st.markdown(f"「{openers[0]}」")
            if st.button("让 TA 主动开口", use_container_width=True):
                state.append_chat(role_name, "assistant", openers[0], initiative=openers[0])
                st.rerun()

    prompt = st.chat_input(f"和{role_name}说点什么…")
    if prompt:
        st.session_state["retry"] = None
        try:
            with st.spinner("TA 正在回忆……"):
                st.session_state["last_turn"] = state.chat_once(role_name, prompt)
        except llm.LLMError as exc:
            st.session_state["retry"] = {"text": prompt, "error": str(exc)}
        else:
            for note in st.session_state["last_turn"].get("notes") or []:
                state.flash(note)
        st.rerun()


# ---------------------------------------------------------------------------
# 记忆舱
# ---------------------------------------------------------------------------
with mem_col:
    st.markdown("#### 记忆舱")
    tier_tab, trace_tab, relation_tab, ablation_tab = st.tabs(["三层", "Trace", "关系", "消融"])

    with tier_tab:
        panels.render_stats(snapshot)
        panels.render_short(snapshot)
        panels.render_mid(snapshot, role_name)
        panels.render_long(snapshot, role_name)

    with trace_tab:
        panels.render_trace(st.session_state.get("last_turn"))

    with relation_tab:
        panels.render_relationship(snapshot)
        st.divider()
        panels.render_loops(snapshot, engine, role_name)
        st.divider()
        panels.render_decay_curve(snapshot)

    with ablation_tab:
        panels.render_ablation(engine, role_name)

st.divider()
st.caption(
    "EmoMem · 三层记忆（短期工作记忆 / 中期情景记忆 / 长期语义画像）+ 情绪加权检索 + 记忆冲突消解 + "
    "可解释 Trace + 关系档案驱动的人格漂移 + 未闭环事件主动关怀 + 人设编译器"
)
