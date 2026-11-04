"""EmoMem · 情感陪伴 Agent（Streamlit 入口）。

本文件只负责界面编排：侧边栏（导航 / 角色 / 人设 / 运行模式）＋ 四个视图
（对话 / 记忆舱 / 实验室 / 人格教练）。视觉全部走 `ui/theme.py` 里的 token，
本文件不写 CSS。业务逻辑分别在 `memory_os`（五层记忆）、`persona_forge`
（人设编译器）、`core`（配置 / LLM / 离线演示）与 `ui`（状态与面板渲染）中。
"""

from __future__ import annotations

import time

import streamlit as st

from core import config as cfg
from core import llm
from memory_os import DEFAULT_OPENER as FALLBACK_OPENER
from ui import avatars, chat, forge, panels, state, theme

USER_AVATAR = "🙂"  # 默认头像；真实值来自侧边栏「我的样子」
VIEW_CHAT = "对话"
VIEW_MEMORY = "记忆舱"
VIEW_LAB = "实验室"
VIEW_COACH = "人格教练"


def _reset_turn_state() -> None:
    """清空记忆后，把"上一轮/待回复/重试"这些一次性状态一起抹掉，免得指向已删的记忆。"""
    st.session_state["last_turn"] = None
    st.session_state["retry"] = None
    st.session_state["pending_turn"] = None
    st.session_state["pending_opener"] = None


def _render_memory_maintenance(role_name: str, names: list[str]) -> None:
    """侧边栏「记忆维护」：清空当前/全部角色记忆，删除自定义角色。"""
    st.caption(f"当前引擎：{llm.mode_label()}")
    if st.button("清空当前角色记忆", use_container_width=True):
        state.reset_role_memory(role_name)
        _reset_turn_state()
        state.flash(f"已清空「{role_name}」的记忆与对话")
        st.rerun()
    if st.button("清空全部角色记忆", use_container_width=True):
        state.reset_everything()
        _reset_turn_state()
        state.flash("已清空全部角色记忆")
        st.rerun()
    custom = [name for name in names if name not in state.DEFAULT_ROLE_RAW]
    if custom:
        victim = st.selectbox("删除自定义角色", custom, index=0)
        if st.button("删除该角色", use_container_width=True):
            state.delete_role(victim)
            if victim == role_name:
                st.session_state["role"] = names[0]
            st.rerun()
_SHORT_TIER = {"short": "短期", "mid": "中期", "long": "长期"}


st.set_page_config(
    page_title="EmoMem · 情感陪伴",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

state.init_session()
state.apply_session_key()  # 把本会话自己的 Key 放回上下文（不落盘、不进环境变量）
theme.inject()

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
pending_view = st.session_state.pop("pending_view", None)
if pending_view:  # 别的组件想跳视图时用它：直接改 key="view" 会因为 widget 已实例化而报错
    st.session_state["view"] = pending_view


with st.sidebar:
    theme.brand("五层记忆 · 人设编译器")

    view = st.radio(
        "视图",
        [VIEW_CHAT, VIEW_MEMORY, VIEW_LAB, VIEW_COACH],
        index=0,
        label_visibility="collapsed",
        key="view",
    )

    st.write("")
    if view == VIEW_COACH:
        # 教练页是独立工作台：别让"当前角色"看起来像这次访谈的对象
        st.caption(f"正在建一个新角色，不会动「{role_name}」的记忆。建完回到「对话」就多一个角色可选。")
    picked = st.selectbox("当前角色", names, index=names.index(role_name), disabled=(view == VIEW_COACH))
    if picked != role_name:
        st.session_state["role"] = picked
        st.session_state["last_turn"] = None
        st.session_state["pending_turn"] = None
        st.session_state["self_note_seeded"] = False
        st.rerun()

    if st.button("锻造新角色", use_container_width=True, type="primary"):
        forge.forge_dialog()
    if st.button("和人格教练聊着建角色", use_container_width=True):
        st.session_state["pending_view"] = VIEW_COACH
        st.rerun()

    st.write("")
    st.caption(state.mode_badge())
    _key_problem = state.api_key_problem()
    if _key_problem:
        st.caption(_key_problem)

    with st.expander("运行模式 / API Key", expanded=False):
        st.caption(
            "Key 只存在你自己的浏览器会话里：页面只显示末 4 位指纹，退出浏览器即失效，"
            "不写进 data/，也不进服务端环境变量。"
        )
        st.caption(state.session_key_label())
        if llm.is_demo_mode():
            st.caption(state.demo_hint())
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

    with st.expander("我的样子（头像）", expanded=False):
        st.caption("头像只影响你自己在对话里的样子，不会写进角色卡。")
        # 当前是图片头像时，把「🖼 当前这张图」也放进选项里：既能让控件始终有合法取值，
        # 也能让人一眼看出"我现在用的是图，不是 emoji"。
        _current_avatar = state.user_avatar()
        _avatar_image = avatars.is_image(_current_avatar)
        _avatar_options = list(state.AVATAR_PRESETS) + ([avatars.PLACEHOLDER] if _avatar_image else [])
        _avatar_default = (
            avatars.PLACEHOLDER
            if _avatar_image
            else (_current_avatar if _current_avatar in state.AVATAR_PRESETS else state.AVATAR_PRESETS[0])
        )
        _picked_avatar = st.pills(
            "选一个",
            _avatar_options,
            default=_avatar_default,
            key="user_avatar_pills",
            label_visibility="collapsed",
        )
        if _picked_avatar and _picked_avatar != avatars.PLACEHOLDER and _picked_avatar != _current_avatar:
            state.set_user_avatar(_picked_avatar)
            st.rerun()
        _avatar_col, _avatar_btn = st.columns([2, 1])
        _custom_avatar = _avatar_col.text_input(
            "或者填一个 emoji",
            value="",
            placeholder=state.user_avatar(),
            max_chars=4,
            key=state.avatar_input_key(),
        )
        if _avatar_btn.button("用这个", use_container_width=True) and (_custom_avatar or "").strip():
            state.set_user_avatar(_custom_avatar)
            state.bump_avatar_input()  # 换掉输入框，避免旧值残留
            st.rerun()
        _upload = st.file_uploader(
            "或者上传一张图片当我的头像",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key=state.avatar_upload_key(),
            help=f"会缩到 {cfg.AVATAR_PX}px 存成本地文件（{cfg.AVATAR_DIR.name}/，不进仓库、不上传）。",
        )
        if _upload is not None:
            try:
                state.set_user_avatar(state.save_avatar_upload(_upload.getvalue(), kind="user"))
            except ValueError as exc:
                st.error(str(exc))
            else:
                state.bump_avatar_upload()
                st.rerun()
        st.caption(f"现在的你：{state.avatar_label(state.user_avatar())}")

    with st.expander("主动关怀（TA 要不要先开口）", expanded=False):
        st.caption(
            "开启后，TA 会在你还没说话时，参考「还没聊完的事 / 上次聊到的话题 / 记住的近况」先问一句，"
            "而不是千篇一律的「今天过得怎么样」。"
        )
        _on = st.toggle("允许 TA 主动开口", value=state.proactive_enabled(), key="proactive_toggle")
        if _on != state.proactive_enabled():
            state.set_proactive_enabled(_on)
            st.rerun()

    with st.expander("人设卡（TA 是谁）", expanded=False):
        _role_avatar_col, _role_avatar_btn = st.columns([2, 1])
        _role_avatar = _role_avatar_col.text_input(
            "TA 的头像", value=avatar, max_chars=4, key=f"role_avatar_{role_name}"
        )
        if _role_avatar_btn.button("换头像", use_container_width=True):
            state.set_role_avatar(role_name, _role_avatar)
            st.rerun()
        _role_upload = st.file_uploader(
            "或者上传一张图片当 TA 的头像",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key=state.role_avatar_upload_key(role_name),
            help="同「我的头像」：缩到 96px 存本地，删掉角色时一并清理。",
        )
        if _role_upload is not None:
            try:
                state.set_role_avatar(
                    role_name, state.save_avatar_upload(_role_upload.getvalue(), kind="role", name=role_name)
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                state.bump_avatar_upload()
                st.rerun()
        forge.render_card_preview(card)

    # 教练视图不加载「记忆维护」：那是"清空某个角色"的操作，和正在建的新角色无关，
    # 摆在那里既让人误解，也容易误点。
    if view != VIEW_COACH:
        with st.expander("记忆维护", expanded=False):
            _render_memory_maintenance(role_name, names)


# ---------------------------------------------------------------------------
# 页头：一个视图一个头部（角色名或视图名 + 一行状态 + 右侧规模）
# ---------------------------------------------------------------------------
import html as _html  # noqa: E402  （只用来把动态文本放进页头的极少量 HTML 里）

_stat_row = snapshot.get("stats") or {}
_counts = (
    f"L0 原文 {int((snapshot.get('archive') or {}).get('messages', 0) or 0)} · "
    f"L1 会话 {int(_stat_row.get('turns', 0) or 0)} · "
    f"L2 事实 {int(_stat_row.get('facts', 0) or 0)} · "
    f"L3 快照 {int((snapshot.get('snapshots') or {}).get('count', 0) or 0)} · "
    f"L4 自述 {int((snapshot.get('self') or {}).get('count', 0) or 0)}"
)
_coach_state = st.session_state.get("coach") or {}
_coach_done = len(_coach_state.get("answers") or {})

if view == VIEW_COACH:
    theme.page_head(
        name="人格教练",
        who="五问一步",
        meta="聊着把一个人问出来，收束成人设速写，再走同一条编译流水线",
        right=f"访谈进度 · 第 {min(int(_coach_state.get('index', 0)) + 1, 5)} / 5 问 · 已答 {_coach_done} 问",
    )
elif view == VIEW_MEMORY:
    theme.page_head(
        name="记忆舱",
        who="五层",
        meta="每层记什么、现在有多少、什么时候被注入——记忆不是黑箱",
        right=_counts,
    )
elif view == VIEW_LAB:
    theme.page_head(
        name="实验室",
        who="Trace 与消融",
        meta="把「它为什么记得这句话」和「关掉某一层会怎样」摊开",
        right=_counts,
    )
else:
    theme.page_head(
        name=_html.escape(role_name),
        who="TA",
        meta=(
            f"关系阶段 <b>{_html.escape(str(stage))}</b> · 亲密度 {intimacy:.0f}/100 · "
            f"模型 {_html.escape(cfg.DEEPSEEK_MODEL)}"
        ),
        right=_counts,
    )

flash_notes = state.take_flash()  # 本轮的固化提示，直接贴在对话下方，不用浮层挡住聊天


# ---------------------------------------------------------------------------
# 视图一：对话
# ---------------------------------------------------------------------------
if view == VIEW_CHAT:
    thread = state.role_thread(role_name)
    chat.render_thread(
        thread,
        role_name=role_name,
        role_avatar=avatar,
        user_avatar=state.user_avatar(),
    )
    # 空会话别留半屏空白：给三句可以照着说的话（点了就直接发出去）
    _user_turns = [t for t in thread if t.get("role") == "user"]
    if not _user_turns and not st.session_state.get("pending_turn"):
        st.caption("不知道说什么的话，可以从这三句里挑一句：")
        _starter_cols = st.columns(3)
        for _col, _line in zip(
            _starter_cols,
            ["今天有点累，但也没什么大事。", "我妈最近住院了，我有点撑不住。", "下周三要去面试，心里没底。"],
        ):
            if _col.button(_line, key=f"starter_{_line[:6]}"):
                _priors = state.begin_turn(role_name, _line)
                st.session_state["pending_turn"] = {
                    "role": role_name,
                    "text": _line,
                    "est_emotion": _priors,
                }
                st.rerun()
    if flash_notes:
        _notes = " · ".join(note if len(note) <= 26 else note[:26] + "…" for note in flash_notes[:2])
        st.caption(_notes)

    retry = st.session_state.get("retry")
    if retry:
        st.error(f"这一轮失败了：{retry.get('error')}")
        if st.button("重试这一句", type="primary"):
            try:
                # 用户那句早就上屏了，这里只重跑"模型这一侧"
                st.session_state["last_turn"] = state.finish_turn(role_name, retry["text"])
                st.session_state["retry"] = None
            except llm.LLMError as exc:
                st.session_state["retry"] = {"text": retry["text"], "error": str(exc)}
            st.rerun()

    # 只在"TA 刚说完、还没轮到你"的时候才提议下一句；你刚发出去就先别插话
    pending = st.session_state.get("pending_turn")
    last_role = thread[-1].get("role") if thread else "assistant"
    card = None
    if state.proactive_enabled() and not pending and last_role != "user":
        # 同一段对话只算一次候选：否则页面每 rerun 一次就换一句，还会白烧一次模型调用
        signature = (role_name, len(thread), thread[-1].get("ts") if thread else 0)
        cached = st.session_state.get("proactive_card")
        if not cached or cached.get("signature") != signature:
            try:
                items = engine.proactive_suggestions(role_name, limit=1, polish=False, commit=False)
            except Exception:
                items = []
            cached = None
            if items and items[0].get("opener") != FALLBACK_OPENER:
                cached = {**items[0], "signature": signature}
            st.session_state["proactive_card"] = cached
        card = cached
    # 主动开口必须落在"具体某件事"上：只剩兜底寒暄就干脆不显示（可在侧边栏关掉）
    if card:
        _why = f"<span class='emo-propose-why'>{_html.escape(card.get('reason') or '')}</span>" if card.get("reason") else ""
        st.markdown(
            "<div class='emo-propose'>"
            f"<span class='emo-propose-label'>TA 想先开口 · {_html.escape(card.get('label', '问一句'))}</span>"
            f"<p>{_html.escape(card['opener'])}</p>{_why}</div>",
            unsafe_allow_html=True,
        )
        if st.button("让 TA 说这一句"):
            st.session_state["pending_opener"] = {"role": role_name}
            st.rerun()

    # 点了"让 TA 主动开口"：这一句才值得让模型润色一次，然后先画打字态、再落话
    pending_opener = st.session_state.pop("pending_opener", None)
    if pending_opener and pending_opener.get("role") == role_name:
        try:
            fresh = engine.proactive_suggestions(role_name, limit=1, polish=True, commit=True)
        except Exception:
            fresh = []
        text = fresh[0]["opener"] if fresh else FALLBACK_OPENER
        chat.render_typing(role_name=role_name, role_avatar=avatar)
        time.sleep(cfg.TYPING_MIN_MS / 1000.0)
        state.append_chat(role_name, "assistant", text, initiative=text)
        st.session_state["proactive_card"] = None
        st.rerun()

    # 用户那句已经落盘、上一帧已经画出来了；这一帧再让 TA 想，气泡随后单独出现
    if pending:
        st.session_state["pending_turn"] = None
        if pending.get("role") != role_name:  # 中途换角色就丢掉，别串台
            st.rerun()
        started = time.monotonic()
        chat.render_typing(role_name=role_name, role_avatar=avatar)  # 先把"正在输入"画上去
        try:
            st.session_state["last_turn"] = state.finish_turn(
                role_name, pending["text"], est_emotion=pending.get("est_emotion")
            )
        except llm.LLMError as exc:
            st.session_state["retry"] = {"text": pending["text"], "error": str(exc)}
        else:
            for note in st.session_state["last_turn"].get("notes") or []:
                state.flash(note)
        waiting_ms = (time.monotonic() - started) * 1000.0
        if waiting_ms < cfg.TYPING_MIN_MS:
            time.sleep((cfg.TYPING_MIN_MS - waiting_ms) / 1000.0)
        st.rerun()

    prompt = st.chat_input(
        f"和{role_name}说点什么…" if not pending else f"{role_name}正在回你…",
        disabled=bool(pending),
    )
    if prompt:
        st.session_state["retry"] = None
        # ① 先只写用户这一句：rerun 之后气泡立刻出现，不用等模型
        est_emotion = state.begin_turn(role_name, prompt)
        st.session_state["pending_turn"] = {
            "role": role_name,
            "text": prompt,
            "est_emotion": est_emotion,
        }
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
            st.caption(f"{hint}　想看「为什么记得这句话」→ 左侧切到「记忆舱」或「实验室」")
        else:
            st.caption("这一轮没有命中旧记忆（还没有可复用的部分）——聊到第 4 轮会做一次「睡眠整理」，之后这里会显示注入了什么。")


# ---------------------------------------------------------------------------
# 视图二：记忆舱（五层）
# ---------------------------------------------------------------------------
if view == VIEW_MEMORY:
    layers = st.tabs(
        ["五层总览", "L0 原文库", "L1 会话层", "L2 长期事实", "L3 固化快照", "L4 自我认知", "关系档案"]
    )
    with layers[0]:
        panels.render_layer_overview(snapshot)
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
        panels.render_loops(snapshot, engine, role_name)


# ---------------------------------------------------------------------------
# 视图三：实验室（Trace / 消融）
# ---------------------------------------------------------------------------
if view == VIEW_LAB:
    lab = st.tabs(["本轮 Trace", "消融开关", "记忆体检"])
    with lab[0]:
        panels.render_trace(st.session_state.get("last_turn"))
    with lab[1]:
        panels.render_ablation(engine, role_name)
    with lab[2]:
        panels.render_stats(snapshot)
        panels.render_self_notes(snapshot)
        st.caption("自我认知本轮是否已注入：" + ("是" if st.session_state.get("self_note_seeded") else "否"))

# ---------------------------------------------------------------------------
# 视图四：人格教练（聊着把人设问出来）
# ---------------------------------------------------------------------------
if view == VIEW_COACH:
    with st.container(key="coach"):  # 只为了让这一页和对话页同宽（.st-key-coach 在 theme 里限宽）
        forge.render_coach_panel()

