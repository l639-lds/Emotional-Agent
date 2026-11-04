"""五层记忆的 L0/L3/L4 支撑：原文库检索、固化快照落盘、自我认知维护。

- **L0 原文库**：直接检索 ``data["chats"][角色]`` 的全量原文（永不删除，不注入 Prompt）；
- **L3 固化快照**：每次真正固化把产物写一份 JSON 到 ``<数据目录>/archive/<角色>/<id>.json`` 并记索引；
- **L4 自我认知**：维护 ``SelfNote``（Agent 对自己的叙述），按相似度去重、按上限丢弃最旧。

这一层只做"纯函数 + 数据结构"的事，不碰流式 UI、不发网络请求，因此可以离线确定性单测。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from core.config import CORRECTION_CONFIDENCE, MemoryConfig, clamp
from memory_os import textsim
from memory_os.retrieval import format_time_label
from memory_os.schema import (
    Episode,
    Fact,
    MemorySnapshot,
    RoleMemory,
    SelfNote,
    as_ms,
    normalize_category,
)

# L4 自我叙述的类型枚举（SPEC §8.1）
NOTE_TYPES = ("成长", "关系", "表达", "情绪")

# 项目根目录（用于把快照路径写成 data/archive/... 这种相对形式）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\s]+')


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def archive_root(store_path: str | Path) -> Path:
    """返回快照落盘根目录：记忆文件同级的 ``archive/``（生产即 ``data/archive``）。"""
    return Path(store_path).parent / "archive"


def safe_role(role: str) -> str:
    """把角色名收敛成安全的目录名（Win/Unix 都不允许的字符换成下划线）。"""
    name = _UNSAFE_NAME_RE.sub("_", str(role or "").strip()).strip("._")
    return name or "default"


def relative_path(path: str | Path) -> str:
    """把绝对路径转成相对项目根的 POSIX 形式；失败则原样返回。"""
    try:
        return Path(os.path.relpath(Path(path), PROJECT_ROOT)).as_posix()
    except (OSError, ValueError):
        return str(path)


def snapshot_label(stamp: float) -> str:
    """快照索引用的中文可读时间（如 ``11-04 03:12``）。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(stamp / 1000.0))
    except (OSError, ValueError, OverflowError):
        return ""


def _message_list(data: Any, role: str) -> list[dict[str, Any]]:
    """取出某角色的全量原文消息列表（缺失/脏数据返回空列表）。"""
    chats = data.get("chats") if isinstance(data, dict) else None
    messages = chats.get(role) if isinstance(chats, dict) else None
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


def _message_ts(message: dict[str, Any]) -> float:
    """取消息时间戳（毫秒）；缺失或非法返回 0.0（不超过注入了的 now）。"""
    raw = message.get("ts")
    try:
        stamp = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return stamp * 1000.0 if 0 < stamp < 1e11 else max(0.0, stamp)


# ---------------------------------------------------------------------------
# L0 原文库
# ---------------------------------------------------------------------------
def search_archive(data: dict[str, Any], role: str, query: str, *,
                   limit: int = 5, now: float | None = None) -> list[dict[str, Any]]:
    """在 L0 原文库里检索历史消息，返回按相似度降序的命中列表。"""
    stamp = as_ms(now)
    messages = _message_list(data, role)
    if not messages:
        return []
    rows: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        text = " ".join(str(message.get("content") or "").split())
        if not text:
            continue
        score = textsim.best_cosine(query, text)
        if score <= 0.0:
            continue
        sent_at = _message_ts(message)
        rows.append(
            {
                "id": f"msg_{index:04d}",
                "ts": sent_at,
                "ts_label": format_time_label(sent_at, stamp) if sent_at else "时间未知",
                "sender": "assistant" if str(message.get("role")) == "assistant" else "user",
                "text": text[:200],
                "score": round(clamp(score, 0.0, 1.0), 4),
            }
        )
    rows.sort(key=lambda item: (-item["score"], -item["ts"]))
    return rows[: max(1, limit)]


def archive_stats(data: dict[str, Any], role: str, *,
                  now: float | None = None) -> dict[str, Any]:
    """统计 L0 原文库规模（条数 + 首末消息时间标签）。"""
    stamp = as_ms(now)
    messages = _message_list(data, role)
    stamps = [ts for ts in (_message_ts(message) for message in messages) if ts > 0]
    return {
        "messages": len(messages),
        "user_messages": sum(1 for m in messages if str(m.get("role")) != "assistant"),
        "first_label": format_time_label(min(stamps), stamp) if stamps else "",
        "last_label": format_time_label(max(stamps), stamp) if stamps else "",
    }


# ---------------------------------------------------------------------------
# L4 自我认知
# ---------------------------------------------------------------------------
def normalize_note_type(raw: Any) -> str:
    """把 note_type 收敛到四类之一，未知归「成长」。"""
    text = str(raw or "").strip()
    return text if text in NOTE_TYPES else "成长"


def note_payload(note: SelfNote, *, now: float | None = None) -> dict[str, Any]:
    """把 SelfNote 渲染成 UI/查询用的 dict（含中文时间标签）。"""
    stamp = as_ms(now)
    return {
        "id": note.id,
        "content": note.content,
        "note_type": note.note_type,
        "created_label": format_time_label(note.created_at, stamp),
        "mention_count": note.mention_count,
    }


def self_note_payloads(memory: RoleMemory, *, limit: int = 3,
                       now: float | None = None) -> list[dict[str, Any]]:
    """取最近的若干条自我叙述（按更新时间倒序，最多 ``limit`` 条）。"""
    stamp = as_ms(now)
    notes = sorted(memory.self_notes, key=lambda item: item.updated_at or item.created_at,
                   reverse=True)
    return [note_payload(note, now=stamp) for note in notes[: max(0, limit)] if note.content]


def apply_self_note(memory: RoleMemory, entry: Any, *, now: float | None = None,
                    config: MemoryConfig | None = None) -> SelfNote | None:
    """写入一条自我叙述：高度相似只刷新计数，否则新增并按上限丢弃最旧。"""
    if not isinstance(entry, dict):
        return None
    cfg = config or MemoryConfig()
    stamp = as_ms(now)
    content = " ".join(str(entry.get("content") or "").split())[:120]
    if not content:
        return None
    merge_sim = clamp(float(getattr(cfg, "self_note_merge_sim", 0.8) or 0.0), 0.0, 1.0)
    for note in memory.self_notes:
        if textsim.similarity(note.content, content) >= merge_sim:
            note.mention_count += 1
            note.updated_at = stamp
            return note
    note = SelfNote(
        content=content,
        note_type=normalize_note_type(entry.get("note_type")),
        created_at=stamp,
        updated_at=stamp,
    )
    memory.self_notes.append(note)
    limit = max(1, int(getattr(cfg, "self_note_max", 40) or 40))
    if len(memory.self_notes) > limit:
        memory.self_notes.sort(key=lambda item: item.created_at)
        memory.self_notes = memory.self_notes[-limit:]
    return note


def render_self_section(notes: list[dict[str, Any]]) -> str:
    """渲染注入 Prompt 的「【它的自我叙述】」小节（没有内容返回空串）。"""
    lines = [f"- {str(item.get('content') or '').strip()}" for item in notes]
    lines = [line for line in lines if line != "-"]
    if not lines:
        return ""
    return "【它的自我叙述】\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# L3 固化快照
# ---------------------------------------------------------------------------
def snapshot_payload(snap: MemorySnapshot) -> dict[str, Any]:
    """把快照索引项渲染成 UI 用的 dict。"""
    return {
        "id": snap.id,
        "label": snap.label,
        "episode_title": snap.episode_title,
        "facts_added": snap.facts_added,
        "facts_updated": snap.facts_updated,
        "self_note": snap.self_note,
    }


def list_snapshot_payloads(memory: RoleMemory, *, limit: int = 5) -> list[dict[str, Any]]:
    """按时间倒序返回快照索引（最多 ``limit`` 条）。"""
    ordered = sorted(memory.snapshots, key=lambda item: item.created_at, reverse=True)
    return [snapshot_payload(snap) for snap in ordered[: max(0, limit)]]


def write_snapshot(memory: RoleMemory, *, role: str, episode: Episode | None,
                   facts_added: list[str], facts_updated: list[str],
                   self_note: SelfNote | None, notes: list[str],
                   now: float | None = None, config: MemoryConfig | None = None,
                   archive_dir: str | Path | None = None) -> MemorySnapshot:
    """把本次固化产物落盘成快照 JSON 并写索引；写盘失败只记 notes，绝不影响主流程。"""
    cfg = config or MemoryConfig()
    stamp = as_ms(now)
    limit = max(1, int(getattr(cfg, "snapshot_max", 30) or 30))
    snap = MemorySnapshot(
        created_at=stamp,
        label=snapshot_label(stamp),
        episode_id=episode.id if episode else "",
        episode_title=episode.title if episode else "",
        facts_added=len(facts_added),
        facts_updated=len(facts_updated),
        self_note=self_note.content if self_note else "",
    )
    payload = {
        "id": snap.id,
        "created_at": stamp,
        "episode": episode.to_dict() if episode else {},
        "facts_added": list(facts_added),
        "facts_updated": list(facts_updated),
        "self_note": snap.self_note,
        "notes": list(notes),
    }
    if archive_dir is not None:
        try:
            folder = Path(archive_dir) / safe_role(role)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{snap.id}.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            snap.path = relative_path(target)
        except (OSError, ValueError) as exc:  # 快照只是留档，失败不能影响固化
            notes.append(f"快照写入失败：{str(exc)[:120]}")
    memory.snapshots.append(snap)
    if len(memory.snapshots) > limit:
        memory.snapshots.sort(key=lambda item: item.created_at)
        dropped = memory.snapshots[:-limit]
        memory.snapshots = memory.snapshots[-limit:]
        for old in dropped:
            _remove_file(old.path)
    return snap


def _remove_file(relative: str) -> None:
    """删除被挤出索引的旧快照文件（失败忽略）。"""
    if not relative:
        return
    try:
        target = Path(relative)
        if not target.is_absolute():
            target = PROJECT_ROOT / target
        target.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# L2 纠正（用户亲手改正一条事实）
# ---------------------------------------------------------------------------
def apply_correction(memory: RoleMemory, fact_id: str, new_content: str, *,
                     category: str | None = None, now: float | None = None,
                     config: MemoryConfig | None = None) -> Fact | None:
    """用户纠正一条事实：新建高置信事实、旧事实 ``superseded_by`` 指向新 id（留档）。"""
    cfg = config or MemoryConfig()
    text = " ".join(str(new_content or "").split())[:160]
    if not text:
        return None
    target = next((fact for fact in memory.facts if fact.id == fact_id), None)
    if target is None:
        return None
    stamp = as_ms(now)
    corrected = Fact(
        content=text,
        category=normalize_category(category or target.category),
        confidence=clamp(float(getattr(cfg, "correction_confidence", CORRECTION_CONFIDENCE)),
                         0.0, 1.0),
        evidence_count=target.evidence_count + 1,
        first_seen=stamp,
        last_confirmed=stamp,
        scope=target.scope,
        base_strength=1.0,
        origin="user_correction",
    )
    memory.facts.append(corrected)
    target.superseded_by = corrected.id
    return corrected


__all__ = [
    "NOTE_TYPES",
    "PROJECT_ROOT",
    "apply_correction",
    "apply_self_note",
    "archive_root",
    "archive_stats",
    "list_snapshot_payloads",
    "normalize_note_type",
    "note_payload",
    "relative_path",
    "render_self_section",
    "safe_role",
    "search_archive",
    "self_note_payloads",
    "snapshot_label",
    "snapshot_payload",
    "write_snapshot",
]
