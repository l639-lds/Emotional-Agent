"""EmoMem · 情感陪伴 Agent（Streamlit 入口）。

本文件只负责界面编排：侧边栏（角色 / 人设 / 运行模式）＋ 三个视图
（💬 对话 / 🧠 记忆舱 / 🧪 实验室）。业务逻辑分别在 `memory_os`（五层记忆）、
`persona_forge`（人设编译器）、`core`（配置 / LLM / 离线演示）与 `ui`（状态与面板渲染）中。
"""

from __future__ import annotations

import streamlit as st

from core import config as cfg
from core import llm
from ui import forge, panels, state

USER_AVATAR = "🙂"
VIEW_CHAT = "💬 对话"
VIEW_MEMORY = "🧠 记忆舱"
VIEW_LAB = "🧪 实验室"
_SHORT_TIER = {"short": "短期", "mid": "中期", "long": "长期"}

st.set_page_config(
    page_title="EmoMem · 情感陪伴",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

state.init_session()
state.apply_session_key()  # 把本会话自己的 Key 放回上下文（不落盘、不进环境变量）

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
    st.session_state["self_note_seeded"] = False

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
    st.caption("五层记忆 · 人设编译器")
    if llm.is_demo_mode():
        st.warning(state.api_key_hint(), icon="🎬")
    else:
        st.success(state.api_key_hint(), icon="🔌")
    _key_problem = state.api_key_problem()
    if _key_problem:
        st.error(_key_problem, icon="🔑")

    with st.expander("运行模式 / API Key", expanded=llm.is_demo_mode()):
        st.caption(
            "Key 只存在你自己的浏览器会话里：页面只显示末 4 位指纹，别处看不到明文，"
            "退出浏览器即失效，也不会写进 data/ 或日志。"
        )
        st.caption(state.session_key_label())
        if llm.is_demo_mode():
            st.caption(
                "没有 Key 也能跑：离线演示模式会用本地假模型把五层记忆、固化、人设编译全链路跑通。"
                "想用服务端公共 Key（部署方配置），在终端设 `$env:DEEPSEEK_API_KEY=\"sk-...\"` 后重启（cmd 才用 `set`）。"
            )
        key_input = st.text_input(
            "DeepSeek API Key（只用于本会话）",
            type="password",
            placeholder="sk-...",
            key=state.key_input_key(),
        )
        _apply_col, _clear_col = st.columns(2)
        with _apply_col:
            if st.button("应用 Key", use_container_width=True):
                state.set_api_key(key_input)
                state.bump_key_input()  # 换掉密码框，明文不留在页面 DOM 里
                st.rerun()
        with _clear_col:
            if st.button("清除 Key", use_container_width=True):
                state.clear_api_key()
                state.bump_key_input()
                st.rerun()

    st.divider()
    picked = st.selectbox("当前角色", names, index=names.index(role_name))
    if picked != role_name:
        st.session_state["role"] = picked
        st.session_state["last_turn"] = None
        st.session_state["self_note_seeded"] = False
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
# 顶部：角色标题 + 视图切换
# ---------------------------------------------------------------------------
head_left, head_right = st.columns([3, 2])
with head_left:
    st.markdown(f"### {avatar} {role_name}")
    st.caption(f"关系阶段 **{stage}** · 亲密度 {intimacy:.0f}/100 · 调用模型 {cfg.DEEPSEEK_MODEL}")
with head_right:
    stat_row = snapshot.get("stats") or {}
    st.caption(
        f"L0 原文 {int((snapshot.get('archive') or {}).get('messages', 0) or 0)} 条 · "
        f"L1 会话 {int(stat_row.get('turns', 0) or 0)} 条 · "
        f"L2 事实 {int(stat_row.get('facts', 0) or 0)} 条 · "
        f"L3 快照 {int((snapshot.get('snapshots') or {}).get('count', 0) or 0)} 份 · "
        f"L4 自述 {int((snapshot.get('self') or {}).get('count', 0) or 0)} 条"
    )

for message in state.take_flash():
    st.toast(message, icon="🧠")

view = st.radio(
    "视图",
    [VIEW_CHAT, VIEW_MEMORY, VIEW_LAB],
    index=0,
    horizontal=True,
    label_visibility="collapsed",
    key="view",
)


# ---------------------------------------------------------------------------
# 视图一：对话
# ---------------------------------------------------------------------------
if view == VIEW_CHAT:
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

    last_turn = st.session_state.get("last_turn") or {}
    if last_turn:
        bundle = last_turn.get("bundle")
        trace = getattr(bundle, "trace", None)
        items = list(getattr(trace, "items", []) or [])
        loops = list(getattr(bundle, "loops", []) or [])
        tiers = sorted({_SHORT_TIER.get(getattr(item, "tier", ""), "") for item in items} - {""})
        if items or loops:
            hint = f"这一轮注入了 {len(items)} 条记忆"
            if loops:
                hint += f" + {len(loops)} 件未闭环的事"
            if tiers:
                hint += f"（覆盖 {' / '.join(tiers)} 层）"
            if last_turn.get("self_note_used"):
                hint += " · 本轮首次唤起 TA 的自我认知"
            st.caption(f"{hint}　想看「为什么记得这句话」→ 切到 🧠 记忆舱 · 🧪 实验室")
        else:
            st.caption("这一轮还没有可复用的记忆——聊到第 4 轮会自动做一次「睡眠整理」，之后这里会显示注入了什么。")


# ---------------------------------------------------------------------------
# 视图二：记忆舱（五层）
# ---------------------------------------------------------------------------
if view == VIEW_MEMORY:
    st.markdown("#### 🧠 记忆舱")
    st.caption("五层记忆各自记什么、现在有多少、什么时候被注入——记忆不是黑箱。")

    layers = st.tabs(
        ["五层总览", "L0 原文库", "L1 会话层", "L2 长期事实", "L3 固化快照", "L4 自我认知", "关系档案"]
    )
    with layers[0]:
        panels.render_layer_overview(snapshot)
        st.divider()
        panels.render_decay_curve(snapshot)
    with layers[1]:
        panels.render_archive(engine, role_name, snapshot)
    with layers[2]:
        panels.render_short(snapshot)
        panels.render_mid(snapshot, role_name)
    with layers[3]:
        panels.render_long(snapshot, role_name)
    with layers[4]:
        panels.render_snapshots(snapshot)
    with layers[5]:
        panels.render_self_notes(snapshot)
    with layers[6]:
        panels.render_relationship(snapshot)
        st.divider()
        panels.render_loops(snapshot, engine, role_name)


# ---------------------------------------------------------------------------
# 视图三：实验室（Trace / 消融）
# ---------------------------------------------------------------------------
if view == VIEW_LAB:
    st.markdown("#### 🧪 实验室")
    st.caption("把「它为什么记得这句话」「关掉某一层会怎样」摊开给评委看。")
    lab = st.tabs(["本轮 Trace", "消融开关", "记忆体检"])
    with lab[0]:
        panels.render_trace(st.session_state.get("last_turn"))
    with lab[1]:
        panels.render_ablation(engine, role_name)
    with lab[2]:
        panels.render_stats(snapshot)
        st.divider()
        panels.render_self_notes(snapshot)
        st.caption("自我认知本轮是否已注入：" + ("是" if st.session_state.get("self_note_seeded") else "否"))

st.divider()
st.caption(
    "EmoMem · 五层记忆（L0 原文库 / L1 会话层 / L2 长期事实 / L3 固化快照 / L4 自我认知）+ "
    "情绪加权检索 + 记忆冲突消解 + 可解释 Trace + 关系档案驱动的人格漂移 + "
    "未闭环事件主动关怀 + 记忆可纠正 + 人设编译器"
)
