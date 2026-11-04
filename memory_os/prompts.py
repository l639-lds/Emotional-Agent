"""记忆抽取 / 固化 / 主动关怀的 Prompt 模板与离线正则兜底。

本模块只负责「文本 → 结构化 JSON」的契约描述，不含业务写库逻辑：
- :data:`CONSOLIDATE_SYSTEM`：一次调用同时产出 Episode / Fact / OpenLoop / 关系更新；
- :func:`build_consolidate_user_prompt`：把本轮新增对话、已有事实、未闭环事件拼成用户消息；
- :func:`heuristic_extract`：LLM 不可用时的**纯正则兜底**，输出与 LLM 同构的 payload，
  consolidate 拿到它之后走完全一致的写库流程（只是 ``used_llm=False``）。
"""

from __future__ import annotations

import re
from typing import Any

from core.config import CATEGORIES, EMOTION_INTENSITY, NEGATIVE_EMOTIONS
from core.demo import detect_emotion, heuristic_extract_turns

# 情绪 → 效价（与 core.demo 的假模型保持一致，便于离线复现）
_EMOTION_VALENCE = {"开心": 0.6, "难过": -0.55, "生气": -0.5, "平静": 0.0}

_NICKNAME_RE = re.compile(r"叫我([\u4e00-\u9fffA-Za-z]{1,6})")
_PROMISE_RE = re.compile(r"(?:答应|约好|说好|保证)([^，。！？\n]{2,18})")
_EMO_LOOP_RE = re.compile(r"(?:我|他|她)(?:一直|还是|总是)(?:怕|担心|放不下)([^，。！？\n]{2,14})")


CONSOLIDATE_SYSTEM = """你是情感陪伴 Agent 的「记忆固化器」。你的任务是把一段刚发生的对话，
压缩成结构化的长期记忆条目。你只输出一个 JSON 对象，不要输出解释、不要用 Markdown 代码块。

## 输出 Schema（严格照此，键名不得改动）
{
  "episode": {
    "title": "一句话事件标题，8-20 字，写清发生了什么",
    "summary": "2-3 句摘要，客观陈述用户说了什么、情绪如何、Agent 做了什么",
    "topics": ["3-6 个话题词，名词短语，不要整句"],
    "keywords": ["用于日后检索的词：专名、地点、事件、时间线索等，3-8 个"],
    "valence": -1.0 到 1.0 的浮点数（负面情绪为负）,
    "arousal": 0.0 到 1.0 的浮点数（情绪激烈程度）,
    "importance": 0.0 到 1.0 的浮点数（是否值得长期记住）
  },
  "facts": [
    {"content": "关于用户的一句话事实，第三人称，必须以「用户」开头",
     "category": "身份|喜好|厌恶|重要人物|长期目标|近况|禁忌|约定",
     "confidence": 0.0 到 1.0,
     "scope": "private 或 shared"}
  ],
  "loops_open": [{"what": "待跟进事项的陈述句，可直接拿去提问", "kind": "待办|承诺|悬念|情绪牵挂",
                  "due_hint": "时间线索，如「下周三」；没有就写 null"}],
  "loops_close": [{"id": "已有未闭环事件的 id"} 或 {"what": "该事项原文"}],
  "nickname": "用户希望被怎么称呼；没有就写 null",
  "relationship": {"intimacy_delta": 0 到 3, "trust_delta": 0 到 2,
                   "themes": ["反复出现的共同话题"], "inside_joke": "只有彼此懂的梗或 null"},
  "self_note": {"content": "你对自己在这段关系里的一点变化的自述，第一人称、一句话",
                "note_type": "成长|关系|表达|情绪"} 或 null
}

## 硬性规则
1. 只记录**用户自己说出的信息**，不要编造，不要把 Agent 的话当用户事实。
2. facts 每条只写一个事实，最长 40 字；「用户喜欢猫」而不是「用户提到过猫」。
3. 分类只能取上面 8 个值；拿不准就用「近况」。
4. 事实与已有事实**矛盾**时才输出与旧事实明显不同的表述（系统会自动做冲突消解），
   不要为了保险把同一件事重复输出两遍。
5. scope：身份/重要人物/长期目标/禁忌类客观信息可以写 shared，其余写 private。
6. 未闭环事件只在**确实没有结果**时输出；用户说事情已经办完了，就写进 loops_close。
7. 本轮对话平淡、没有信息量时：episode 照写（importance 给低分），facts 与 loops_open 给空数组。
8. self_note 只在**你自己（Agent）的表达方式或关系状态确实有变化**时才写，写"我"不写"用户"，
   最长 40 字；没有就写 null。不要复述事实。
9. 只输出 JSON，不要输出任何其它字符。"""


def format_turn_list(turns: list[dict[str, Any]], limit: int = 40) -> str:
    """把 turns 渲染成 ``user: ...`` 的纯文本，供 Prompt 使用。"""
    lines: list[str] = []
    for turn in turns[-limit:]:
        if not isinstance(turn, dict):
            continue
        speaker = "用户" if turn.get("role") == "user" else "Agent"
        content = str(turn.get("content", "")).strip().replace("\n", " ")
        if content:
            lines.append(f"{speaker}：{content[:200]}")
    return "\n".join(lines) if lines else "（本轮没有有效对话）"


def build_consolidate_user_prompt(turns: list[dict[str, Any]],
                                  facts: list[dict[str, Any]] | None = None,
                                  loops: list[dict[str, Any]] | None = None,
                                  nickname: str | None = None) -> str:
    """拼装固化调用的用户消息：新对话 + 已有记忆摘要 + 输出要求提醒。"""
    facts = facts or []
    loops = loops or []
    fact_lines = [
        f"- [{item.get('category', '近况')}] {item.get('content', '')}"
        for item in facts[:12]
        if str(item.get("content", "")).strip()
    ]
    loop_lines = [
        f"- {item.get('what', '')}（{item.get('kind', '待办')}，未闭环）"
        for item in loops[:8]
        if str(item.get("what", "")).strip()
    ]
    sections = [
        "## 本轮新增对话",
        format_turn_list(turns),
        "## 已有长期事实（仅用于判断「合并 / 冲突 / 新增」，不要重复输出）",
        "\n".join(fact_lines) if fact_lines else "（暂无）",
        "## 已有未闭环事件（若本轮已解决，请写进 loops_close）",
        "\n".join(loop_lines) if loop_lines else "（暂无）",
        f"## 当前称呼\n{nickname or '（尚未约定称呼）'}",
        "## 输出\n只输出符合 Schema 的 JSON 对象。",
    ]
    return "\n\n".join(sections)


PROACTIVE_SYSTEM = """你是情感陪伴 Agent 的「主动关怀器」。基于用户未闭环的事件，
生成一句自然、具体的开场白，像是你们上次聊到一半的延续。
要求：
1. 只输出 JSON：{"opener": "一句中文开场白", "reason": "为什么挑这一件事"};
2. 不要提「记忆」「系统」「记录」这类词，不要说教，不要凑感叹号；
3. 一件事一句话，20-40 字，先关心结果再关心情绪；
4. 若有时间线索（如「下周三」），要把它自然带进来。
不要输出任何其它字符。"""


def build_proactive_user_prompt(loops: list[dict[str, Any]]) -> str:
    """拼装主动关怀调用的用户消息。"""
    loop_lines = [
        f"- {item.get('what', '')}｜类型：{item.get('kind', '待办')}｜时间线索：{item.get('due_hint') or '无'}"
        for item in loops[:8]
    ]
    body = "\n".join(loop_lines) if loop_lines else "（用户最近没有未闭环的事）"
    return f"## 未闭环事件\n{body}\n\n## 输出\n只输出符合要求的 JSON 对象。"


# ---------------------------------------------------------------------------
# 离线正则兜底
# ---------------------------------------------------------------------------


def _first_user_text(turns: list[dict[str, Any]]) -> str:
    for turn in turns:
        if isinstance(turn, dict) and turn.get("role") == "user":
            text = str(turn.get("content", "")).strip()
            if text:
                return text
    return ""


def _guess_nickname(turns: list[dict[str, Any]]) -> str | None:
    """从「叫我小明」这类句式里猜一个称呼。"""
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        match = _NICKNAME_RE.search(str(turn.get("content", "")))
        if match:
            return match.group(1)[:8]
    return None


def heuristic_extract(turns: list[dict[str, Any]], *,
                      fallback_summary: str = "") -> dict[str, Any]:
    """纯正则兜底：产出与 LLM 同构的固化 payload（不依赖网络、确定性）。

    Args:
        turns: ``[{"role","content"}]`` 形式的新增对话。
        fallback_summary: 短期层的滚动摘要，用于 episode.summary 兜底。

    Returns:
        与 :data:`CONSOLIDATE_SYSTEM` 约定的 JSON 结构一致（可能字段偏少，交由上层逐项降级）。
    """
    clean_turns = [turn for turn in turns if isinstance(turn, dict)]
    user_texts = [
        str(turn.get("content", "")).strip()
        for turn in clean_turns
        if turn.get("role") == "user" and str(turn.get("content", "")).strip()
    ]
    joined = " ".join(user_texts)
    emotion = detect_emotion(joined)
    arousal = EMOTION_INTENSITY.get(emotion, 0.35)
    valence = _EMOTION_VALENCE.get(emotion, 0.0)

    extract = heuristic_extract_turns(clean_turns)
    loops_open = list(extract.get("loops_open", []))
    for pattern, kind in ((_PROMISE_RE, "承诺"), (_EMO_LOOP_RE, "情绪牵挂")):
        for match in pattern.finditer(joined):
            what = match.group(1).strip()[:24]
            if what and not any(what in loop.get("what", "") for loop in loops_open):
                loops_open.append({"what": what, "kind": kind, "due_hint": None})
    loops_open = loops_open[:4]

    title_seed = (user_texts[0][:16] if user_texts else "一段闲聊").strip() or "一段闲聊"
    summary = fallback_summary.strip()[:120]
    detail = "；".join(text[:24] for text in user_texts[:3])
    summary = f"{summary}｜本轮：{detail}"[:200] if summary else f"用户提到：{detail}"[:200]

    keywords: list[str] = []
    for source in (joined, title_seed):
        for word in re.split(r"[，。！？、\s,.!?]+", source):
            word = word.strip()
            if 2 <= len(word) <= 8 and word not in keywords:
                keywords.append(word)
    topics = keywords[:4] or ["日常"]

    importance = 0.4 + 0.06 * len(user_texts) + 0.12 * (emotion in NEGATIVE_EMOTIONS)
    importance += 0.08 if loops_open else 0.0
    importance = round(min(0.95, importance), 2)

    return {
        "episode": {
            "title": f"关于「{title_seed}」",
            "summary": summary,
            "topics": topics,
            "keywords": (keywords + topics)[:8],
            "valence": round(valence, 2),
            "arousal": round(min(1.0, arousal + 0.05 * len(user_texts)), 2),
            "importance": importance,
        },
        "facts": list(extract.get("facts", [])),
        "loops_open": loops_open,
        "loops_close": [],
        "nickname": _guess_nickname(clean_turns),
        "relationship": {
            "intimacy_delta": 1,
            "trust_delta": 0,
            "themes": topics[:2],
            "inside_joke": None,
        },
        # L4 自我认知需要"自我观察"，规则兜底不臆造，直接留空（SPEC §8.2）
        "self_note": None,
        "_source": "heuristic",
    }


def describe_categories() -> str:
    """把分类枚举渲染成「/」分隔的中文串，供 Prompt 与 UI 复用。"""
    return "｜".join(CATEGORIES)


__all__ = [
    "CONSOLIDATE_SYSTEM",
    "PROACTIVE_SYSTEM",
    "build_consolidate_user_prompt",
    "build_proactive_user_prompt",
    "describe_categories",
    "format_turn_list",
    "heuristic_extract",
]
