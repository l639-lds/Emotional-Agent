"""旧版角色兼容层：把 v1 的 ``{persona, opening, avatar}`` 包装成 :class:`PersonaCard`。

老用户的历史数据只有两个字符串，直接丢掉太可惜；这里做**无损包装**——

- 原文一字不改地存进 ``card.legacy_raw``（``persona`` / ``opening`` / ``description`` / ``avatar``）；
- ``persona`` 同时写进 ``backstory``，让新渲染管线在未重新编译时也能照原样扮演；
- ``legacy=True``，UI 可据此提示「这个角色还是旧版，建议点『重新编译』体验人设卡」。

包装结果**不做任何加工**（不编造口头禅、不伪造台词锚点），因为编造出来的细节会污染旧角色的语气。
"""

from __future__ import annotations

import re
from typing import Any

from persona_forge.schema import (
    PersonaCard,
    SpeechProfile,
    as_int,
    as_text,
)

# 旧版卡片的默认头像（旧数据里经常没有 avatar）
DEFAULT_LEGACY_AVATAR = "🎭"

# 旧版字段的候选键名（不同历史版本用过不同命名）
_PERSONA_KEYS = ("persona", "system_prompt", "prompt", "persona_prompt")
_OPENING_KEYS = ("opening", "greeting", "first_message", "opening_line", "welcome")
_DESC_KEYS = ("description", "desc", "user_input", "input")
_AVATAR_KEYS = ("avatar", "emoji", "icon")


def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    """按候选键名取出第一个非空文本。"""
    for key in keys:
        text = as_text(raw.get(key), limit=4000)
        if text:
            return text
    return ""


def _estimate_sentence_len(text: str) -> int:
    """从旧版原文粗估平均句长（按中文句末标点切分），估不出来返回 0。"""
    sentences = [part for part in re.split(r"[。！？!?\n]+", text or "") if part.strip()]
    if not sentences:
        return 0
    return as_int(len(text) / len(sentences), 0, low=0, high=80)


def from_legacy(name: str, raw: Any) -> PersonaCard:
    """把旧版角色数据包装成 PersonaCard，**永不抛异常**。

    Args:
        name: 角色名（旧版数据里通常存在角色列表的键上，不在 raw 里）。
        raw: 旧版字典，典型形如 ``{"persona": "...", "opening": "...", "avatar": "🐱"}``；
            也接受纯字符串（当成 persona 原文）。

    Returns:
        ``legacy=True`` 的 PersonaCard：原文保存在 ``legacy_raw``，``persona`` 原文同时进入
        ``backstory``，``opening`` 原文进入 ``relationship_arc["陌生"]``（旧角色的开场白就是它
        对陌生人的态度）。
    """
    if isinstance(raw, str):
        raw = {"persona": raw}
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}

    persona_text = _first_text(data, _PERSONA_KEYS)
    opening_text = _first_text(data, _OPENING_KEYS)
    description_text = _first_text(data, _DESC_KEYS)
    avatar_text = _first_text(data, _AVATAR_KEYS) or DEFAULT_LEGACY_AVATAR

    safe_name = as_text(name, limit=24) or as_text(data.get("name"), limit=24) or "旧版角色"
    # 一句话定位：取 persona 原文的第一句，截断到 24 字以内，不加修饰
    first_sentence = re.split(r"[。！？!?\n]", persona_text)[0].strip() if persona_text else ""
    tagline = as_text(first_sentence, limit=24) or "旧版角色（尚未编译为结构化人设）"

    legacy_raw: dict[str, str] = {}
    if persona_text:
        legacy_raw["persona"] = persona_text
    if opening_text:
        legacy_raw["opening"] = opening_text
    if description_text:
        legacy_raw["description"] = description_text
    legacy_raw["avatar"] = avatar_text

    return PersonaCard(
        name=safe_name,
        tagline=tagline,
        avatar=avatar_text,
        identity={"age_vibe": "", "occupation": "", "setting": ""},
        backstory=persona_text or description_text,
        values=[],
        speech=SpeechProfile(
            register="旧版人设原文（未编译）：按原文语气说话，不要自行改写",
            avg_sentence_len=_estimate_sentence_len(persona_text),
            tics=[],
            punctuation="",
            banned_words=[],
            # 旧版没有台词锚点：不伪造，留空由渲染层给出「自拟」提示
            sample_lines=[],
        ),
        emotion_map=[],
        relationship_arc={
            "陌生": opening_text or "（旧版未提供开场白）",
            "熟悉": "",
            "亲近": "",
            "亲密": "",
        },
        boundaries=[],
        anti_patterns=[],
        memory_hooks=[],
        taboos=[],
        legacy=True,
        legacy_raw=legacy_raw,
    )


def legacy_opening(card: PersonaCard) -> str:
    """取出旧版角色的开场白（非旧版返回空串）。"""
    if not card.legacy:
        return ""
    return as_text(card.legacy_raw.get("opening"), limit=2000)


__all__ = ["DEFAULT_LEGACY_AVATAR", "from_legacy", "legacy_opening"]
