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

from core import config as cfg

_CSS = """
<style>
.emo-thread { display: flex; flex-direction: column; gap: 14px; margin: 6px 0 10px; }
.emo-row { display: flex; gap: 10px; align-items: flex-start; }
.emo-row-user { justify-content: flex-end; }
.emo-avatar { width: 40px; height: 40px; flex: 0 0 40px; border-radius: 50%; background: #F1F5F9;
  display: flex; align-items: center; justify-content: center; font-size: 22px; line-height: 1;
  box-shadow: 0 1px 2px rgba(15,23,42,.12); user-select: none; }
.emo-body { display: flex; flex-direction: column; gap: 4px; max-width: 72%; min-width: 0; }
.emo-row-user .emo-body { align-items: flex-end; }
.emo-name { font-size: 12px; color: #64748B; }
.emo-bubble { padding: 10px 14px; border-radius: 14px; font-size: 15px; line-height: 1.75;
  color: #0F172A; white-space: pre-wrap; word-break: break-word;
  box-shadow: 0 1px 2px rgba(15,23,42,.08); }
.emo-bubble-assistant { background: #FFFFFF; border: 1px solid #E2E8F0; border-top-left-radius: 4px; }
.emo-bubble-user { background: #DCFCE7; border: 1px solid #BBF7D0; border-top-right-radius: 4px; }
.emo-meta { font-size: 11px; color: #64748B; line-height: 1.5; }
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
    """助手气泡下方的小字：情绪 / 追问 / 待办 / 记忆更新。"""
    parts: list[str] = []
    emotion = message.get("emotion")
    if emotion:
        parts.append(f"{cfg.EMOTION_EMOJI.get(emotion, '')} 你当时的情绪：{emotion}")
    if message.get("initiative"):
        parts.append(f"↪ TA 追问：{message['initiative']}")
    if message.get("loop_hint"):
        parts.append(f"📌 记下待办：{message['loop_hint']}")
    if message.get("memory_update"):
        parts.append(f"🧠 本轮记忆更新：{message['memory_update']}")
    return " · ".join(parts)


def row_html(message: dict[str, Any], *, role_name: str, role_avatar: str, user_avatar: str) -> str:
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
        pieces.append(f'<div class="emo-avatar">{_escape(avatar)}</div>')
    pieces.append('<div class="emo-body">')
    header = name if not stamp else f"{name} · {stamp}"
    pieces.append(f'<div class="emo-name">{_escape(header)}</div>')
    pieces.append(f'<div class="emo-bubble {side}">{_escape(message.get("content", ""))}</div>')
    if meta:
        pieces.append(f'<div class="emo-meta">{_escape(meta)}</div>')
    pieces.append("</div>")
    if is_user:
        pieces.append(f'<div class="emo-avatar">{_escape(avatar)}</div>')
    pieces.append("</div>")
    return "".join(pieces)


def render_thread(
    messages: Iterable[dict[str, Any]],
    *,
    role_name: str,
    role_avatar: str,
    user_avatar: str,
) -> None:
    """整段对话一次画完（一次性 markdown，避免 Streamlit 在气泡之间插空行）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
    rows = [
        row_html(message, role_name=role_name, role_avatar=role_avatar, user_avatar=user_avatar)
        for message in messages
    ]
    if not rows:
        st.caption("还没有开始聊。说一句试试？")
        return
    st.markdown(f'<div class="emo-thread">{"".join(rows)}</div>', unsafe_allow_html=True)
