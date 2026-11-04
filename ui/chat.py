"""对话气泡渲染：用户消息靠右、助手消息靠左，头像可换。

为什么要自己画 HTML：Streamlit 原生的 ``st.chat_message`` 一律左对齐，做不出
"自己的话在右边"这种 IM 习惯；而气泡内容全部是用户/模型文本，所以这里统一
``html.escape`` 再做极少的换行替换，绝不把文本当 HTML 执行。
"""

from __future__ import annotations

import html
import time
from typing import Any, Iterable

import streamlit as st

from ui import avatars, theme

_CSS = f"""
<style>
.emo-thread {{ display: flex; flex-direction: column; gap: 26px; margin: 4px 0 26px; }}
.emo-row {{ display: flex; gap: 14px; align-items: flex-start; }}
.emo-row-user {{ flex-direction: row; justify-content: flex-end; }}
.emo-avatar {{ width: 30px; height: 30px; flex: 0 0 30px; border-radius: 50%; overflow: hidden;
  display: flex; align-items: center; justify-content: center; font-size: 17px; line-height: 1;
  background: {theme.SURFACE_2}; color: {theme.INK}; user-select: none; margin-top: 2px; }}
.emo-avatar img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.emo-body {{ display: flex; flex-direction: column; gap: 6px; max-width: 66ch; min-width: 0; }}
.emo-row-user .emo-body {{ align-items: flex-end; }}
.emo-name {{ font-size: 13px; color: {theme.FAINT}; letter-spacing: 0.01em; }}
.emo-bubble {{ font-size: 15px; line-height: 1.78; padding: 13px 16px; border-radius: 2px;
  white-space: pre-wrap; word-break: break-word; }}
.emo-bubble-assistant {{ background: {theme.SURFACE_1}; color: {theme.INK}; }}
.emo-bubble-user {{ background: {theme.SURFACE_2}; color: {theme.INK}; }}
.emo-meta {{ font-size: 13px; color: {theme.FAINT}; line-height: 1.65; }}
.emo-read {{ font-size: 13px; color: {theme.FAINT}; }}
.emo-typing {{ display: inline-flex; align-items: center; gap: 6px; padding: 16px; }}
.emo-typing span {{ width: 5px; height: 5px; border-radius: 50%; background: {theme.MUTED};
  display: inline-block; animation: emo-blink 1.2s infinite ease-in-out; }}
.emo-typing span:nth-child(2) {{ animation-delay: .18s; }}
.emo-typing span:nth-child(3) {{ animation-delay: .36s; }}
@keyframes emo-blink {{
  0%, 60%, 100% {{ opacity: .25; transform: translateY(0); }}
  30% {{ opacity: 1; transform: translateY(-2px); }}
}}
@media (prefers-reduced-motion: reduce) {{
  .emo-typing span {{ animation: none; opacity: .6; }}
}}
</style>
"""


def _escape(text: str) -> str:
    """转义成安全 HTML（保留换行，靠 CSS 的 pre-wrap 呈现）。"""
    return html.escape(text or "", quote=False)


def _stamp(value: Any) -> str:
    """毫秒时间戳 → ``11-04 13:05``。"""
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(seconds))


def meta_line(message: dict[str, Any]) -> str:
    """助手气泡下方的小字：情绪 / 补一句 / 待办 / 记忆更新。"""
    parts: list[str] = []
    emotion = message.get("emotion")
    if emotion:
        parts.append(f"你当时听上去：{emotion}")
    if message.get("initiative"):
        parts.append(f"补一句：{message['initiative']}")
    if message.get("loop_hint"):
        parts.append(f"记下待办：{message['loop_hint']}")
    if message.get("memory_update"):
        parts.append(f"记忆更新：{message['memory_update']}")
    return " · ".join(parts)


def row_html(message: dict[str, Any], *, role_name: str, role_avatar: str, user_avatar: str,
             read: bool = False) -> str:
    """一条消息 → 一段 HTML（用户靠右、助手靠左）。"""
    is_user = message.get("role") == "user"
    avatar = user_avatar if is_user else role_avatar
    name = "你" if is_user else role_name
    stamp = _stamp(message.get("ts") or message.get("created_at"))
    side = "emo-bubble-user" if is_user else "emo-bubble-assistant"
    meta = "" if is_user else meta_line(message)
    pieces = [
        f'<div class="emo-row{" emo-row-user" if is_user else ""}">',
    ]
    if not is_user:
        pieces.append(avatars.html_avatar(avatar))
    pieces.append('<div class="emo-body">')
    header = name if not stamp else f"{name} · {stamp}"
    pieces.append(f'<div class="emo-name">{_escape(header)}</div>')
    pieces.append(f'<div class="emo-bubble {side}">{_escape(message.get("content", ""))}</div>')
    if is_user and read:
        pieces.append('<div class="emo-read">已读</div>')
    if meta:
        pieces.append(f'<div class="emo-meta">{_escape(meta)}</div>')
    pieces.append("</div>")
    if is_user:
        pieces.append(avatars.html_avatar(avatar))
    pieces.append("</div>")
    return "".join(pieces)


def typing_html(*, role_name: str, role_avatar: str) -> str:
    """「对方正在输入…」的气泡：三颗闪烁的点，让等待看起来像真人在打字。"""
    return "".join([
        '<div class="emo-row">',
        avatars.html_avatar(role_avatar),
        '<div class="emo-body">',
        f'<div class="emo-name">{_escape(role_name)}</div>',
        '<div class="emo-bubble emo-bubble-assistant emo-typing">'
        "<span></span><span></span><span></span></div>",
        '<div class="emo-read">正在输入…</div>',
        "</div></div>",
    ])


def render_typing(*, role_name: str, role_avatar: str) -> None:
    """把打字态画在当前对话下面（写盘/等模型之前调用，用户立刻能看到反馈）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        f'<div class="emo-col"><div class="emo-thread">'
        f'{typing_html(role_name=role_name, role_avatar=role_avatar)}</div></div>',
        unsafe_allow_html=True,
    )


def render_thread(
    messages: Iterable[dict[str, Any]],
    *,
    role_name: str,
    role_avatar: str,
    user_avatar: str,
) -> None:
    """整段对话一次画完（一次性 markdown，避免 Streamlit 在气泡之间插空行）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
    rows: list[str] = []
    ordered = list(messages)
    for index, message in enumerate(ordered):
        is_user = message.get("role") == "user"
        later_reply = any(
            other.get("role") == "assistant" and str(other.get("content") or "").strip()
            for other in ordered[index + 1:]
        )
        rows.append(
            row_html(
                message,
                role_name=role_name,
                role_avatar=role_avatar,
                user_avatar=user_avatar,
                read=bool(is_user and later_reply),
            )
        )
    if not rows:
        st.caption("还没有开始聊。说一句试试？")
        return
    st.markdown(
        f'<div class="emo-col"><div class="emo-thread">{"".join(rows)}</div></div>',
        unsafe_allow_html=True,
    )
