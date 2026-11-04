"""LLM 访问层：DeepSeek（OpenAI 兼容）调用的唯一入口。

设计目标：
1. 全项目只在这里碰网络，便于统一做 JSON 兜底、重试、脱敏与离线演示。
2. 所有「结构化」调用通过 :func:`call_json` 完成，调用方只关心 `purpose`。
3. 没配置 API Key 或显式开启 `EMO_DEMO_MODE=1` 时自动切换为离线演示模式，
   保证无网络也能把三层记忆 / 人设编译器的整条链路跑通（答辩现场兜底）。
"""

from __future__ import annotations

import json
import os
import re
import time
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Callable, Optional

from openai import OpenAI

from core.config import (
    ALLOWED_EMOTIONS,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    EMOTION_ALIASES,
    MAX_CONTEXT_MESSAGES,
)

# 结构化调用签名：
# caller(system_prompt, user_prompt, *, temperature=?, max_tokens=?, purpose=?, demo_context=?) -> dict
JsonCaller = Callable[..., dict[str, Any]]


class LLMError(RuntimeError):
    """LLM 调用或结构化输出解析失败（消息已脱敏，可直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 基础查询
# ---------------------------------------------------------------------------


def redact(text: Any) -> str:
    """隐藏错误信息中可能出现的密钥片段。"""
    detail = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", str(text))
    return detail[:1500]


def key_looks_placeholder(value: str) -> bool:
    """Key 是否像"写错了/还是占位符"。

    典型手滑：①把文档里的 ``sk-xxxx`` 一起粘进去；②PowerShell 里用了 cmd 的
    ``set NAME=value``（环境变量没设上，这条只是兜底）；③漏了 ``sk-`` 前缀。
    这种情况按"没配置"处理，直接降级到离线演示模式，比拿坏 Key 反复报错友好。
    """
    value = value.strip()
    if not value:
        return False
    if "xxxx" in value.lower() or "你的" in value or "<" in value:
        return True
    if value.count("sk-") > 1:
        return True
    if not value.startswith("sk-"):
        return True
    return len(value) < 20


def configured() -> bool:
    """是否配置了可用的 DeepSeek API Key（占位符/明显写错的 Key 视为未配置）。"""
    value = active_api_key()
    return bool(value) and not key_looks_placeholder(value)


# ---------------------------------------------------------------------------
# 会话级 API Key：默认只存在于"输入它的人"这一个浏览器会话里
#
# Streamlit 的服务进程是所有访客共享的，所以绝不能把访客输入的 Key 写进
# os.environ（那是进程级，等于全服共享、还看不见谁在用）。这里用 ContextVar：
# 每次 rerun 由 UI 层把本会话的 Key 放进来，LLM 调用在同一线程内读它，
# 天然隔离到"这一个会话"，且不落盘、不进日志。
# ---------------------------------------------------------------------------

_SESSION_KEY: ContextVar[str] = ContextVar("emo_session_api_key", default="")


def set_session_api_key(value: str) -> None:
    """把当前会话的 Key 放进上下文（空串表示清除）。"""
    _SESSION_KEY.set((value or "").strip())


def session_api_key() -> str:
    """当前会话自己的 Key（未设置则为空）。"""
    return _SESSION_KEY.get().strip()


def env_api_key() -> str:
    """服务端环境变量里的 Key（部署时给"公共演示额度"用）。"""
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def active_api_key() -> str:
    """本次调用实际使用的 Key：会话级优先，其次服务端环境变量。"""
    return session_api_key() or env_api_key()


def key_fingerprint(value: Optional[str] = None) -> str:
    """Key 的指纹，只用于界面确认"我连的是哪把 Key"，绝不回显明文。"""
    raw = (value if value is not None else active_api_key()).strip()
    if not raw:
        return ""
    tail = raw[-4:] if len(raw) >= 4 else raw
    return f"sk-…{tail}"


def key_source() -> str:
    """Key 的来源：session / env / none（界面上用来提醒"这是你自己的 Key"）。"""
    if session_api_key():
        return "session"
    if env_api_key():
        return "env"
    return "none"


@lru_cache(maxsize=8)
def _client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=60.0,
        max_retries=1,
    )


def _get_client() -> OpenAI:
    api_key = active_api_key()
    if not api_key:
        raise LLMError("未配置 DEEPSEEK_API_KEY")
    return _client(api_key)


def forced_demo() -> bool:
    """服务端是否显式打开了 ``EMO_DEMO_MODE=1``（答辩兜底用）。"""
    return os.getenv("EMO_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def is_demo_mode() -> bool:
    """离线演示模式：这个会话没填 Key，且（服务端强制 或 没有任何可用 Key）。

    优先级：**访客本会话填的 Key 最高**。他愿意用自己的 Key 就是想用真模型，
    不该被部署方启动时的 ``EMO_DEMO_MODE=1`` 挡住——那个开关的语义是
    "没人填 Key 时用本地假模型兜底"，不是"禁止联网"。
    """
    own = session_api_key()
    if own and not key_looks_placeholder(own):
        return False
    if forced_demo():
        return True
    return not configured()


def mode_label() -> str:
    """给 UI 显示的模式名（含 Key 指纹与来源，但不回显明文）。"""
    if is_demo_mode():
        if forced_demo():
            return "离线演示模式（EMO_DEMO_MODE=1 强制；在本页填一个 Key 就会切回在线）"
        return "离线演示模式（还没填 Key，本地假模型全链路可跑）"
    source = "本会话" if key_source() == "session" else "服务端"
    return f"在线模型 {DEEPSEEK_MODEL} · {source} Key {key_fingerprint()}"


# ---------------------------------------------------------------------------
# JSON 解析兜底（沿用原 app.py 已验证的实现）
# ---------------------------------------------------------------------------


def extract_json_objects(text: str) -> list[str]:
    """提取文本中花括号配对完整的 JSON 对象，忽略字符串内的花括号。"""
    objects: list[str] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None

    return objects


def parse_json_object(content: Optional[str]) -> dict[str, Any]:
    """把模型输出解析为 JSON 对象，兼容代码围栏、前后杂文本和字符串内换行。"""
    if not content or not content.strip():
        raise LLMError("模型返回了空内容")

    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    candidates = [text, *extract_json_objects(text)]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            data = json.loads(candidate, strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data

    raise LLMError("模型返回的内容不是合法的 JSON 对象")


def normalize_emotion(raw: Any) -> str:
    """把任意情绪写法归一为四类之一，未知值回落到「平静」。"""
    emotion = str(raw).strip() if raw is not None else "平静"
    emotion = EMOTION_ALIASES.get(emotion, emotion)
    return emotion if emotion in ALLOWED_EMOTIONS else "平静"


def clean_memory_text(raw: Any) -> Optional[str]:
    """把 memory_update 之类的字段标准化为字符串或 None。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        value = raw.strip()
        if value.lower() in {"", "null", "none", "无", "没有"}:
            return None
        return value
    return json.dumps(raw, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 结构化调用（全项目唯一 LLM 入口）
# ---------------------------------------------------------------------------


def call_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.6,
    max_tokens: int = 1600,
    purpose: str = "json",
    demo_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """请求一段 JSON 输出并解析成 dict。

    Args:
        system_prompt: 系统提示词（包含 JSON 协议约束）。
        user_prompt: 本轮用户内容。
        temperature: 采样温度；重试时逐步降低。
        max_tokens: 输出上限。
        purpose: 调用用途，离线演示模式据此分发假数据。
        demo_context: 离线演示模式所需的结构化上下文（见 docs/SPEC.md §4.1），
            真实调用时忽略。

    Raises:
        LLMError: 三次尝试后仍然失败。
    """
    if is_demo_mode():
        from core import demo

        return demo.fake_json(
            purpose=purpose,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            demo_context=demo_context,
        )

    client = _get_client()
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=max(0.1, temperature - 0.2 * attempt),
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:  # 网络/鉴权/限流
            last_error = exc
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
            continue

        if not response.choices:
            last_error = LLMError("模型没有返回候选结果")
            continue

        choice = response.choices[0]
        if choice.finish_reason == "length":
            last_error = LLMError("模型输出因长度限制被截断")
            continue

        try:
            return parse_json_object(choice.message.content)
        except LLMError as exc:
            last_error = exc

    raise LLMError(f"[{purpose}] 结构化调用失败：{redact(last_error)}")


# ---------------------------------------------------------------------------
# 主对话调用
# ---------------------------------------------------------------------------


def wrap_history_message(message: dict[str, str]) -> dict[str, str]:
    """把历史 assistant 文本包装成 JSON 再回放，保持 JSON Mode 的格式一致性。

    如果历史里的 assistant 是纯文本，模型很容易模仿纯文本格式，导致
    JSON Mode 把非法输出替换成空白内容（finish_reason 仍可能是 stop）。
    """
    if message.get("role") != "assistant":
        return {"role": message["role"], "content": message["content"]}
    return {
        "role": "assistant",
        "content": json.dumps(
            {"reply": message["content"], "emotion": "平静", "memory_update": None},
            ensure_ascii=False,
        ),
    }


def structured_chat(
    system_prompt: str,
    messages: list[dict[str, str]],
    *,
    purpose: str = "chat",
) -> dict[str, Any]:
    """主对话调用：返回模型原始 JSON（由上层校验 reply/emotion 等字段）。

    保留原实现的容错策略：JSON Mode + 最多 3 次重试 + 第三次只带 system 与最后一条用户消息。
    """
    from core import demo

    if is_demo_mode():
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return demo.fake_chat(system_prompt=system_prompt, user_message=last_user)

    client = _get_client()
    system_message = {"role": "system", "content": system_prompt}
    history = [system_message, *[wrap_history_message(m) for m in messages[-MAX_CONTEXT_MESSAGES:]]]

    last_user_content = next(
        (message["content"] for message in reversed(messages) if message["role"] == "user"),
        "",
    )
    fallback_history = [system_message]
    if last_user_content:
        fallback_history.append({"role": "user", "content": last_user_content})

    last_error: Optional[Exception] = None
    for attempt in range(3):
        request_messages = history if attempt < 2 or not last_user_content else fallback_history
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=request_messages,
                response_format={"type": "json_object"},
                temperature=0.8 if attempt == 0 else 0.5,
                max_tokens=2000,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
            continue

        if not response.choices:
            last_error = LLMError("模型没有返回候选结果")
            continue

        choice = response.choices[0]
        if choice.finish_reason == "length":
            last_error = LLMError("模型输出因长度限制被截断")
            continue

        try:
            return parse_json_object(choice.message.content)
        except LLMError as exc:
            last_error = exc

    raise LLMError(f"[{purpose}] 模型返回的 JSON 无法解析：{redact(last_error)}")
