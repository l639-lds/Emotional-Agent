"""UI 状态层：角色 / 人设卡 / 记忆引擎的装配与持久化。

这一层只做数据搬运与流程编排，不渲染任何视觉组件，便于单独测试与复用。
所有「写盘」都走原子写；所有 LLM 调用都经 :mod:`core.llm` 或组件包内部。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Optional

import streamlit as st

from core import config as cfg
from ui import avatars
from core import demo, llm
from memory_os import MemoryEngine, MemoryStore
from persona_forge import card_to_dict, compile_persona, dict_to_card
from persona_forge.legacy import from_legacy
from persona_forge.schema import PersonaCard
from persona_forge.render import build_system_prompt

ROLE_FILE_VERSION = 3

# 内置角色：沿用 v1 的 persona 文案，包装成 legacy 人设卡，保证老用户体验不变
DEFAULT_ROLE_RAW: dict[str, dict[str, str]] = {
    "温柔老父亲": {
        "avatar": "👨",
        "opening": "孩子，今天过得怎么样？坐下来慢慢说，爸爸听着呢。",
        "persona": (
            "温柔、包容、有生活智慧的老父亲，说话慢条斯理，"
            "喜欢用“哎”或“哈哈”开头。无论用户说什么，都先接纳他的情绪，"
            "不评判、不指责、不急着说教；关心要具体，可以称呼用户为“孩子”。"
        ),
    },
    "傲娇女儿": {
        "avatar": "👧",
        "opening": "哼，你终于来了。有话快说，我才没有一直在等你呢。",
        "persona": (
            "嘴硬心软的傲娇女儿，表面上有点不耐烦、爱逞强，实际上很在意用户，"
            "会认真听用户说话。语气自然、俏皮，可以偶尔嘴硬，"
            "但绝不能刻薄、贬低或攻击用户。"
        ),
    },
}


# ---------------------------------------------------------------------------
# session_state
# ---------------------------------------------------------------------------


def init_session() -> None:
    """初始化所有会话级默认值（幂等）。"""
    ss = st.session_state
    ss.setdefault("role", None)
    ss.setdefault("flash", [])
    ss.setdefault("retry", None)
    ss.setdefault("last_turn", None)
    ss.setdefault("pending_turn", None)  # 用户已上屏、还没拿到回复的那一句
    ss.setdefault("forge_result", None)
    ss.setdefault("coach", None)  # 人格教练的对话进度
    ss.setdefault("view", "对话")
    ss.setdefault("self_note_seeded", False)
    ss.setdefault("api_key", "")  # 访客自己填的 Key：只活在会话里
    ss.setdefault("api_key_nonce", 0)
    ss.setdefault("avatar_upload_nonce", 0)  # 密码框的 key 后缀：提交后换一个，等于清空输入框
    for key in ("ab_mid", "ab_long", "ab_mood_gate", "ab_conflict", "ab_auto_consolidate"):
        ss.setdefault(key, True)


def flash(text: str) -> None:
    """登记一条待展示的提示（同一次 rerun 内展示后清空）。"""
    if text:
        st.session_state["flash"].append(text)


def take_flash() -> list[str]:
    """取出并清空待展示提示。"""
    items = list(st.session_state.get("flash") or [])
    st.session_state["flash"] = []
    return items


# ---------------------------------------------------------------------------
# 角色文件（v3：卡片结构化存储，兼容 v1 的 persona/opening 纯文本）
# ---------------------------------------------------------------------------


def _empty_role_file() -> dict[str, Any]:
    return {"version": ROLE_FILE_VERSION, "roles": {}}


def load_role_file() -> dict[str, Any]:
    """读取自定义角色文件；损坏或缺失时返回空结构，绝不抛异常。"""
    path = cfg.CUSTOM_ROLES_FILE
    if not path.exists():
        return _empty_role_file()
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_role_file()
    if not isinstance(raw, dict):
        return _empty_role_file()
    roles_raw = raw.get("roles", raw)
    if not isinstance(roles_raw, dict):
        return _empty_role_file()
    roles = {k: v for k, v in roles_raw.items() if isinstance(k, str) and isinstance(v, dict)}
    return {"version": ROLE_FILE_VERSION, "roles": roles}


def save_role_file(data: dict[str, Any]) -> None:
    """原子写入自定义角色文件。"""
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": ROLE_FILE_VERSION, "roles": data.get("roles", {})}
    temp_file = cfg.CUSTOM_ROLES_FILE.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(cfg.CUSTOM_ROLES_FILE)


def role_names(role_file: Optional[dict[str, Any]] = None) -> list[str]:
    """内置角色在前，自定义角色在后，保持稳定顺序。"""
    data = role_file if role_file is not None else load_role_file()
    custom = [name for name in data["roles"] if name not in DEFAULT_ROLE_RAW]
    return [*DEFAULT_ROLE_RAW.keys(), *custom]


def raw_role(name: str, role_file: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """取角色的原始配置（先自定义、后内置）。"""
    data = role_file if role_file is not None else load_role_file()
    if name in data["roles"]:
        return data["roles"][name]
    return DEFAULT_ROLE_RAW.get(name)


def get_card(name: str, role_file: Optional[dict[str, Any]] = None) -> PersonaCard:
    """取角色的人设卡：结构化卡片直接还原，v1 纯文本包装成 legacy 卡。"""
    raw = raw_role(name, role_file) or {}
    card_raw = raw.get("card")
    if isinstance(card_raw, dict):
        return dict_to_card(card_raw)
    fallback = {"persona": f"你扮演「{name}」，用自然、有情绪温度的人话回应用户。", "opening": "……"}
    merged = {**fallback, **{k: v for k, v in raw.items() if isinstance(v, str)}}
    return from_legacy(name, merged)


def opening_for(name: str, card: PersonaCard, role_file: Optional[dict[str, Any]] = None) -> str:
    """角色的开场白：优先用文件里的 opening，其次用台词锚点。"""
    raw = raw_role(name, role_file) or {}
    opening = raw.get("opening")
    if isinstance(opening, str) and opening.strip():
        return opening.strip()
    samples = list(getattr(getattr(card, "speech", None), "sample_lines", []) or [])
    if samples and isinstance(samples[0], str) and samples[0].strip():
        return samples[0].strip()
    tagline = getattr(card, "tagline", "")
    return tagline.strip() or "……"


def avatar_for(name: str, card: PersonaCard, role_file: Optional[dict[str, Any]] = None) -> str:
    """角色头像：优先文件配置，其次卡片字段。"""
    raw = raw_role(name, role_file) or {}
    avatar = raw.get("avatar") or getattr(card, "avatar", None)
    return avatar.strip() if isinstance(avatar, str) and avatar.strip() else "🙂"


def save_forged_role(
    name: str,
    card: PersonaCard,
    *,
    knobs: Optional[dict[str, float]] = None,
    score: Optional[int] = None,
) -> None:
    """保存人设编译器产出的卡片。"""
    data = load_role_file()
    data["roles"][name] = {
        "card": card_to_dict(card),
        "opening": opening_for(name, card),
        "avatar": avatar_for(name, card),
        "meta": {
            "source": "forge",
            "knobs": dict(knobs or {}),
            "score": score,
            "created_at": time.time() * 1000.0,
        },
    }
    save_role_file(data)


def delete_role(name: str) -> bool:
    """删除一个自定义角色（内置角色不可删）。"""
    data = load_role_file()
    if name not in data["roles"]:
        return False
    gone = data["roles"].pop(name, None) or {}
    save_role_file(data)
    if avatars.is_image(gone.get("avatar")):
        avatars.discard(gone.get("avatar"))  # 角色删了，它的头像图也一起清掉
    return True


# ---------------------------------------------------------------------------
# 记忆引擎装配
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_store() -> MemoryStore:
    """全进程共享一个 MemoryStore（只持有路径，读写仍走磁盘）。"""
    return MemoryStore(cfg.MEMORY_FILE)


def ablation_config() -> cfg.MemoryConfig:
    """把侧边栏的消融开关翻译成 MemoryConfig。"""
    ss = st.session_state
    return cfg.MemoryConfig(
        mood_gate=bool(ss.get("ab_mood_gate", True)),
        conflict_resolution=bool(ss.get("ab_conflict", True)),
        auto_consolidate=bool(ss.get("ab_auto_consolidate", True)),
    )


def enabled_tiers() -> set[str]:
    """当前启用记忆层级（消融开关）。"""
    tiers = {"short"}
    if st.session_state.get("ab_mid", True):
        tiers.add("mid")
    if st.session_state.get("ab_long", True):
        tiers.add("long")
    return tiers


def build_engine() -> MemoryEngine:
    """按当前消融开关装配一个引擎实例（每次 rerun 重新加载，保证读到的都是最新盘上数据）。"""
    return MemoryEngine(get_store(), config=ablation_config())


# ---------------------------------------------------------------------------
# 对话线程（chats 仍沿用 v2 结构，UI 按它渲染气泡）
# ---------------------------------------------------------------------------


def role_thread(role: str) -> list[dict[str, Any]]:
    """读取某个角色的完整对话线程。"""
    data = get_store().load()
    raw = (data.get("chats") or {}).get(role)
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, dict) and isinstance(m.get("content"), str)]


def append_chat(
    role: str,
    sender: str,
    content: str,
    *,
    emotion: Optional[str] = None,
    initiative: Optional[str] = None,
    loop_hint: Optional[str] = None,
    memory_update: Optional[str] = None,
) -> None:
    """追加一条消息并立即落盘。"""
    store = get_store()
    data = store.load()
    chats = data.setdefault("chats", {})
    thread = chats.setdefault(role, [])
    item: dict[str, Any] = {"role": sender, "content": content, "ts": time.time() * 1000.0}
    if emotion:
        item["emotion"] = emotion
    if initiative:
        item["initiative"] = initiative
    if loop_hint:
        item["loop_hint"] = loop_hint
    if memory_update:
        item["memory_update"] = memory_update
    thread.append(item)
    store.save(data)


def append_exchange(
    role: str,
    user_text: str,
    user_emotion: Optional[str],
    reply: str,
    *,
    initiative: Optional[str] = None,
    loop_hint: Optional[str] = None,
    memory_update: Optional[str] = None,
) -> None:
    """一轮问答一次性落盘（user + assistant 两条），避免两次读写互相覆盖。"""
    store = get_store()
    data = store.load()
    chats = data.setdefault("chats", {})
    thread = chats.setdefault(role, [])
    now = time.time() * 1000.0
    thread.append({"role": "user", "content": user_text, "ts": now, "emotion": user_emotion or "平静"})
    assistant: dict[str, Any] = {"role": "assistant", "content": reply, "ts": now + 1.0}
    if initiative:
        assistant["initiative"] = initiative
    if loop_hint:
        assistant["loop_hint"] = loop_hint
    if memory_update:
        assistant["memory_update"] = memory_update
    thread.append(assistant)
    store.save(data)


def ensure_opening(name: str, card: PersonaCard) -> None:
    """空线程时补上开场白，避免界面空白。"""
    if role_thread(name):
        return
    append_chat(name, "assistant", opening_for(name, card))


def reset_role_memory(name: str) -> None:
    """清空某个角色的全部记忆与对话。"""
    engine = build_engine()
    engine.reset_role(name)
    store = get_store()
    data = store.load()
    chats = data.setdefault("chats", {})
    chats[name] = []
    store.save(data)
    st.session_state["self_note_seeded"] = False


def reset_everything() -> None:
    """清空全部角色记忆与对话（保留自定义人设与全局共享事实）。"""
    store = get_store()
    data = store.load()
    data["roles"] = {}
    data["chats"] = {}
    store.save(data)
    st.session_state["self_note_seeded"] = False


def forget_memory(role: str, item_id: str, *, tier: str = "long") -> bool:
    """让 TA 忘掉一条记忆（记忆主权：可审计，也可删除）。

    ``tier``：``mid`` 删一条情景 Episode，``long`` 删一条语义 Fact，
    ``shared`` 删一条跨角色共享事实。返回是否真的删掉了。
    """
    store = get_store()
    data = store.load()
    target = str(item_id or "")
    if not target:
        return False
    if tier == "shared":
        shared = data.get("shared_facts") or []
        kept = [item for item in shared if str(item.get("id")) != target]
        if len(kept) == len(shared):
            return False
        data["shared_facts"] = kept
        store.save(data)
        return True
    roles = data.get("roles") or {}
    role_data = roles.get(role)
    if not isinstance(role_data, dict):
        return False
    key = "episodes" if tier == "mid" else "facts"
    items = role_data.get(key) or []
    kept = [item for item in items if str(item.get("id")) != target]
    if len(kept) == len(items):
        return False
    role_data[key] = kept
    roles[role] = role_data
    data["roles"] = roles
    store.save(data)
    return True


# ---------------------------------------------------------------------------
# 五层记忆：L0 原文库 / L2 纠正 / L4 自我认知
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")


def _split_sentences(text: str) -> list[str]:
    """把一段话切成句子，用于「按分句最大相似度」打分。"""
    pieces = [piece.strip() for piece in _SENTENCE_SPLIT.split(text or "") if piece and piece.strip()]
    return pieces or [str(text or "").strip()]


def _ts_label(value: Any) -> str:
    """毫秒时间戳 → "11-04 02:10"；非法值返回空串。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(value) / 1000.0))
    except (TypeError, ValueError, OSError):
        return ""


def search_archive(role: str, query: str, *, limit: int = cfg.ARCHIVE_TOP_K) -> list[dict[str, Any]]:
    """L0 原文库检索：在全部历史原文里找跟 ``query`` 最像的几句话。

    原文库永不删除、永不改写——它是记忆的「底稿」，用来回答
    「你到底是在哪句话里记住我的」。
    """
    text = (query or "").strip()
    if not text:
        return []
    engine = build_engine()
    try:
        results = engine.search_archive(role, text, limit=limit)
        return [dict(item) for item in results]
    except (AttributeError, TypeError):
        return _search_archive_locally(role, text, limit)


def _search_archive_locally(role: str, query: str, limit: int) -> list[dict[str, Any]]:
    """引擎还没提供 search_archive 时的等价实现（纯本地相似度）。"""
    from memory_os import similarity

    hits: list[dict[str, Any]] = []
    for index, message in enumerate(role_thread(role)):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        score = max((float(similarity(query, piece)) for piece in _split_sentences(content)), default=0.0)
        if score <= 0.0:
            continue
        hits.append(
            {
                "id": f"chat_{index}",
                "ts": message.get("ts") or 0.0,
                "ts_label": _ts_label(message.get("ts")),
                "sender": "user" if message.get("role") == "user" else "assistant",
                "text": content,
                "score": round(score, 4),
            }
        )
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits[:limit]


def archive_stats(role: str) -> dict[str, Any]:
    """L0 原文库规模：一共存了多少句话、从什么时候开始。"""
    engine = build_engine()
    try:
        return dict(engine.archive_stats(role))
    except (AttributeError, TypeError):
        pass
    thread = role_thread(role)
    stamps = [float(message.get("ts") or 0.0) for message in thread]
    stamps = [stamp for stamp in stamps if stamp > 0]
    return {
        "messages": len(thread),
        "user_messages": sum(1 for message in thread if message.get("role") == "user"),
        "first_label": _ts_label(min(stamps)) if stamps else "",
        "last_label": _ts_label(max(stamps)) if stamps else "",
    }


def self_notes(role: str, *, limit: int = cfg.SELF_NOTE_INJECT_MAX) -> list[dict[str, Any]]:
    """L4 自我认知：它对自己的叙述（最新 ``limit`` 条）。"""
    engine = build_engine()
    try:
        return [dict(item) for item in engine.self_notes(role, limit=limit)]
    except (AttributeError, TypeError):
        pass
    data = get_store().load()
    role_data = (data.get("roles") or {}).get(role) or {}
    raw = role_data.get("self_notes") or []
    notes = []
    for item in raw[-limit:]:
        if not isinstance(item, dict):
            continue
        notes.append(
            {
                "id": str(item.get("id") or ""),
                "content": str(item.get("content") or ""),
                "note_type": str(item.get("note_type") or "成长"),
                "created_label": _ts_label(item.get("created_at")),
                "mention_count": int(item.get("mention_count", 1) or 1),
            }
        )
    return notes


def self_note_pending(role: str) -> bool:
    """L4 是否该在本轮注入：只在新会话首轮（或切换角色后首轮）。"""
    if st.session_state.get("self_note_seeded"):
        return False
    try:
        stats = build_engine().stats(role)
    except Exception:  # 老版本引擎没有 stats 时按「不注入」处理
        return False
    return int(stats.get("turns", 0) or 0) >= cfg.CONSOLIDATE_EVERY_TURNS


def correct_fact(role: str, fact_id: str, new_content: str, *, category: Optional[str] = None) -> bool:
    """L2 记忆纠正：用户亲手改一条事实。

    做法是「新增一条高置信事实 + 把旧事实标为被取代」，而不是原地覆盖——
    这样旧版本仍在文件里留档，审计时能看出「它以前记错过什么」。
    """
    content = (new_content or "").strip()
    target = str(fact_id or "")
    if not content or not target:
        return False
    store = get_store()
    data = store.load()
    roles = data.get("roles") or {}
    role_data = roles.get(role)
    if not isinstance(role_data, dict):
        return False
    facts = role_data.get("facts") or []
    old = next((item for item in facts if isinstance(item, dict) and str(item.get("id")) == target), None)
    if not isinstance(old, dict):
        return False

    now = time.time() * 1000
    new_fact = {
        "id": f"fact_{uuid.uuid4().hex[:10]}",
        "content": content[:160],
        "category": category or str(old.get("category") or "近况"),
        "confidence": cfg.CORRECTION_CONFIDENCE,
        "evidence_count": int(old.get("evidence_count", 1) or 1) + 1,
        "first_seen": old.get("first_seen") or now,
        "last_confirmed": now,
        "scope": str(old.get("scope") or "private"),
        "superseded_by": None,
        "recall_count": 0,
        "base_strength": 1.0,
        "archived": False,
        "origin": "user_correction",
    }
    old["superseded_by"] = new_fact["id"]
    facts.append(new_fact)
    role_data["facts"] = facts
    roles[role] = role_data
    data["roles"] = roles
    store.save(data)
    return True


# ---------------------------------------------------------------------------
# 单轮对话
# ---------------------------------------------------------------------------


def begin_turn(role_name: str, user_text: str) -> str:
    """先把用户这一句落盘，让气泡立刻上屏（不用等模型想完）。返回情绪先验。"""
    est_emotion = demo.detect_emotion(user_text)
    append_chat(role_name, "user", user_text, emotion=est_emotion)
    return est_emotion


def _user_tail_pending(role_name: str, user_text: str) -> bool:
    """用户这句是否已经写进对话线程（即 begin_turn 已经走过）。"""
    thread = role_thread(role_name)
    if not thread:
        return False
    last = thread[-1]
    return last.get("role") == "user" and str(last.get("content") or "").strip() == user_text.strip()


def finish_turn(
    role_name: str,
    user_text: str,
    *,
    est_emotion: Optional[str] = None,
) -> dict[str, Any]:
    """模型这一侧：检索 → 拼 Prompt → 调模型 → 写记忆 → 落下 TA 的回复。

    Raises:
        core.llm.LLMError: 模型调用失败时抛出，由 UI 层展示重试入口。
    """
    engine = build_engine()
    card = get_card(role_name)

    # 用户情绪先验：本地关键词判定，既服务检索门控，也用于气泡配色
    est_emotion = est_emotion or demo.detect_emotion(user_text)
    already_written = _user_tail_pending(role_name, user_text)
    # L4 自我认知只在会话首轮注入：每轮都灌就变成「别人每隔十秒告诉你你是谁」
    include_self = self_note_pending(role_name)
    try:
        bundle = engine.retrieve(
            role_name, user_text, est_emotion, tiers=enabled_tiers(), include_self=include_self
        )
    except TypeError as exc:  # 引擎尚未支持 include_self 时退回三层检索
        if "include_self" not in str(exc):
            raise
        bundle = engine.retrieve(role_name, user_text, est_emotion, tiers=enabled_tiers())
        include_self = False
    try:
        memory_block = engine.build_memory_block(bundle, include_self=include_self)
    except TypeError as exc:
        if "include_self" not in str(exc):
            raise
        memory_block = engine.build_memory_block(bundle)
        include_self = False
    if include_self:
        st.session_state["self_note_seeded"] = True

    relationship = getattr(bundle, "relationship", None)
    intimacy = float(getattr(relationship, "intimacy", 0.0) or 0.0)
    stage = getattr(relationship, "stage", None) or cfg.stage_of(intimacy)
    nickname = getattr(relationship, "nickname", None)
    system_prompt = build_system_prompt(card, memory_block, stage, nickname)

    thread = [
        {"role": m["role"], "content": m["content"]}
        for m in role_thread(role_name)
        if m.get("role") in {"user", "assistant"} and str(m.get("content") or "").strip()
    ]
    if not already_written:  # 界面走 begin_turn 时这句已经在线程里，重复追加会让模型看到两遍
        thread.append({"role": "user", "content": user_text})
    history = thread[-cfg.SHORT_TERM_INJECT_TURNS:]

    raw = llm.structured_chat(system_prompt, history)
    reply = str(raw.get("reply") or "").strip()
    if not reply:
        raise llm.LLMError("模型没有返回有效回复内容")

    emotion = llm.normalize_emotion(raw.get("emotion"))
    initiative = llm.clean_memory_text(raw.get("initiative"))
    loop_hint = llm.clean_memory_text(raw.get("loop_hint"))
    memory_update = llm.clean_memory_text(raw.get("memory_update"))

    # 先写记忆（engine 会整份 load/save），再写对话线程，否则 chats 会被记忆写入覆盖
    engine.record_turn(role_name, user_text, reply, emotion)
    if already_written:  # 用户气泡已经上屏了，这里只补 TA 的回复
        append_chat(
            role_name,
            "assistant",
            reply,
            initiative=initiative,
            loop_hint=loop_hint,
            memory_update=memory_update,
        )
    else:
        append_exchange(
            role_name,
            user_text,
            est_emotion,
            reply,
            initiative=initiative,
            loop_hint=loop_hint,
            memory_update=memory_update,
        )

    consolidation = getattr(engine, "last_consolidation", None)
    notes = list(getattr(consolidation, "notes", []) or []) if consolidation is not None else []
    return {
        "reply": reply,
        "emotion": emotion,
        "est_emotion": est_emotion,
        "initiative": initiative,
        "loop_hint": loop_hint,
        "memory_update": memory_update,
        "bundle": bundle,
        "memory_block": memory_block,
        "system_prompt": system_prompt,
        "consolidation": consolidation,
        "notes": notes,
        "self_note_used": include_self,
    }


def chat_once(role_name: str, user_text: str) -> dict[str, Any]:
    """一步跑完一轮（脚本与测试用；界面走 begin_turn + finish_turn 两段，让气泡先上屏）。"""
    est_emotion = begin_turn(role_name, user_text)
    return finish_turn(role_name, user_text, est_emotion=est_emotion)


def compile_and_preview(description: str, knobs: dict[str, float]) -> Any:
    """人设编译（供 dialog 调用，异常已在编译器内部降级）。"""
    return compile_persona(description, knobs=knobs)


def api_key_problem() -> str:
    """检查当前生效的 Key 是否像"写了但写错"（返回原因，空串表示没问题）。

    最常见的两种手滑：①把占位符 ``sk-xxxx`` 一起粘进去了；
    ②在 PowerShell 里用 cmd 的 ``set NAME=value``，结果环境变量根本没设上。
    """
    value = llm.active_api_key()
    if not value:
        return ""
    if "xxxx" in value.lower() or "你的" in value or value.count("sk-") > 1:
        return "API Key 里还带着占位符（含 xxxx 或重复的 sk- 前缀），请整个替换成真实 Key。"
    if not value.startswith("sk-"):
        return "API Key 不是以 sk- 开头，多半是写错了（PowerShell 要用 $env:DEEPSEEK_API_KEY=\"sk-...\"）。"
    if len(value) < 20:
        return "API Key 太短了，看起来不像完整 Key。"
    return ""


def api_key_hint() -> str:
    """API Key / 演示模式状态文案（只显示模式与 Key 指纹，永不回显明文）。"""
    if llm.is_demo_mode() and not llm.forced_demo():
        return "离线演示模式（本地假模型，全链路可跑）"
    return llm.mode_label()


def mode_badge() -> str:
    """侧边栏那一行状态用的短标签：只讲"现在走哪条路"，细节留在展开面板里。"""
    if llm.session_api_key():
        return f"在线 · 你的 Key {llm.key_fingerprint()}"
    if llm.env_api_key():
        return f"在线 · 服务端 Key {llm.key_fingerprint()}"
    if llm.forced_demo():
        return "离线演示 · 填 Key 即切在线"
    return "离线演示模式"


def demo_hint() -> str:
    """演示模式下的补充说明：什么时候会切回在线、什么时候被强制挡住。"""
    if not llm.is_demo_mode():
        return ""
    if llm.forced_demo():
        return "服务端用 EMO_DEMO_MODE=1 开了强制离线；你在下面填一个自己的 Key，就会立刻切回在线模型。"
    return "没有 Key 也能跑通全链路；想用真模型，在下面填一个 Key 即可（只存在你的浏览器会话里）。"


def avatar_upload_key() -> str:
    """文件上传框的 widget key：带 nonce，存完换一个，避免旧文件一直卡在框里。"""
    return f"user_avatar_upload_{int(st.session_state.get('avatar_upload_nonce', 0) or 0)}"


def role_avatar_upload_key(role_name: str) -> str:
    nonce = int(st.session_state.get("avatar_upload_nonce", 0) or 0)
    return f"role_avatar_upload_{role_name}_{nonce}"


def bump_avatar_upload() -> None:
    """换上新的上传框 key（值不能直接清，widget 已实例化就不许改）。"""
    st.session_state["avatar_upload_nonce"] = int(st.session_state.get("avatar_upload_nonce", 0) or 0) + 1


def save_avatar_upload(data: bytes, *, kind: str, name: str = "") -> str:
    """把上传的头像图存到本地并返回 token；失败抛 ``ValueError``（界面自己提示）。"""
    return avatars.store(data, kind=kind, name=name)


def session_key_label() -> str:
    """侧边栏一句状态：本会话是否已用自己的 Key。"""
    if llm.session_api_key():
        return f"本会话已使用你自己的 Key {llm.key_fingerprint()}（只存在这个浏览器会话里，别人看不到，也不会写进文件）"
    if llm.env_api_key():
        return f"正在使用服务端配置的 Key {llm.key_fingerprint()}（访客看不到明文，也不需要自己填）"
    return "还没有可用的 Key：可以在下面填自己的，或继续用离线演示模式"


def set_api_key(value: str) -> None:
    """把 Key 只放进"当前浏览器会话"。

    - 不进 ``os.environ``：Streamlit 的服务进程是全体访客共享的，写环境变量等于把
      某个人输入的 Key 变成全服共用（别人还能白用他的额度）。
    - 不落盘、不写日志、不在界面上回显明文，只用 ``key_fingerprint()`` 显示末 4 位。
    """
    value = (value or "").strip()
    st.session_state["api_key"] = value
    llm.set_session_api_key(value)


def key_input_key() -> str:
    """密码框的 widget key（带 nonce，应用/清除后换一个，等于把输入框清空）。"""
    return f"api_key_input_{int(st.session_state.get('api_key_nonce', 0))}"


def bump_key_input() -> None:
    """换掉密码框的 key：已提交的明文不会再留在浏览器 DOM 里（避免被旁人点"显示密码"）。"""
    st.session_state["api_key_nonce"] = key_input_nonce() + 1


def key_input_nonce() -> int:
    return int(st.session_state.get("api_key_nonce", 0))


def apply_session_key() -> None:
    """每次 rerun 开头调用：把本会话的 Key 重新放回上下文（Streamlit 每次重跑是新的上下文）。"""
    llm.set_session_api_key(st.session_state.get("api_key", "") or "")


def clear_api_key() -> None:
    """忘掉本会话的 Key（回到服务端 Key 或离线演示模式）。"""
    st.session_state["api_key"] = ""
    llm.set_session_api_key("")

# ---------------------------------------------------------------------------
# 界面偏好（data/settings.json）：我的头像、是否允许 TA 主动开口
#
# 这两个都是"用户自己的偏好"，不是角色数据：换角色应该保留，所以单独存一份。
# 读失败一律回到默认值，绝不让设置文件坏掉时整个应用起不来。
# ---------------------------------------------------------------------------

AVATAR_PRESETS = ["🙂", "🐱", "🐼", "🦊", "🌙", "🍃", "🧑", "👩"]
DEFAULT_USER_AVATAR = "🙂"


def _load_settings() -> dict[str, Any]:
    try:
        raw = json.loads(cfg.SETTINGS_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_settings(**patch: Any) -> None:
    data = _load_settings()
    data.update(patch)
    try:
        cfg.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = cfg.SETTINGS_FILE.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(cfg.SETTINGS_FILE)
    except Exception:
        pass  # 偏好存不下来也不该影响对话


def _clean_avatar(value: Optional[str], *, fallback: str = "") -> str:
    """头像值统一清洗：图片 token 原样保留，其它（emoji/文字）最多留 4 个字符。"""
    text = (value or "").strip()
    if avatars.is_image(text):
        return text
    text = text[:4]
    return text or fallback


def user_avatar() -> str:
    """我在对话里的头像：会话里改过就用会话里的，否则用上次保存的偏好。"""
    value = _clean_avatar(st.session_state.get("user_avatar"))
    if value:
        return value
    return _clean_avatar(_load_settings().get("user_avatar"), fallback=DEFAULT_USER_AVATAR)


def avatar_label(value: Optional[str]) -> str:
    """给 markdown/caption 用的头像文字：图片就显示 🖼。"""
    return avatars.display(value)


def set_user_avatar(value: str) -> None:
    value = _clean_avatar(value, fallback=DEFAULT_USER_AVATAR)
    previous = user_avatar()
    if previous != value and avatars.is_image(previous):
        avatars.discard(previous)  # 旧图没人用了，别把 data/avatars 越堆越大
    st.session_state["user_avatar"] = value
    _save_settings(user_avatar=value)


def proactive_enabled() -> bool:
    """是否允许 TA 在等你说话时主动开口（默认开）。"""
    if "proactive_enabled" in st.session_state:
        return bool(st.session_state["proactive_enabled"])
    return bool(_load_settings().get("proactive_enabled", True))


def set_proactive_enabled(value: bool) -> None:
    st.session_state["proactive_enabled"] = bool(value)
    _save_settings(proactive_enabled=bool(value))

def avatar_input_key() -> str:
    """自定义头像输入框的 key（带 nonce，提交后换一个，避免旧值残留）。"""
    return f"avatar_input_{int(st.session_state.get('avatar_nonce', 0))}"


def bump_avatar_input() -> None:
    st.session_state["avatar_nonce"] = int(st.session_state.get("avatar_nonce", 0)) + 1

def set_role_avatar(name: str, avatar: str) -> None:
    """改角色的头像（emoji）——内置角色也能改，改完只影响这个角色的气泡。"""
    avatar = _clean_avatar(avatar)
    if not avatar:
        return
    data = load_role_file()
    entry = dict(data.get("roles", {}).get(name) or {})
    if not entry:
        base = DEFAULT_ROLE_RAW.get(name) or {}
        entry = {key: value for key, value in base.items() if key in ("opening", "persona", "description")}
    entry["avatar"] = avatar
    data.setdefault("roles", {})[name] = entry
    save_role_file(data)
