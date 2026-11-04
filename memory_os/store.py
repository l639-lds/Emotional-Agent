"""记忆文件的原子读写、v2/旧格式→v3 迁移与容错。

设计要点：
1. **原子写**：先写同目录临时文件再 ``os.replace``，避免进程被杀时留下半截 JSON；
2. **永不崩溃**：缺文件 / 空文件 / 破损 JSON 一律回落默认结构，破损文件重命名成 ``*.bak`` 保留现场；
3. **兼容迁移**：v2（``{"chats":..., "memories":...}``）与更早的平铺格式（``{"角色名": [msgs]}``）
   都自动升级到 v3，并把旧 ``memories: list[str]`` 转成长期 Fact，历史不丢。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from memory_os.schema import Fact, RoleMemory, default_data, now_ms

# 存储版本号（SPEC §2）
STORAGE_VERSION = 3


def _replace_with_retry(source: Path, target: Path, attempts: int = 5) -> None:
    """``os.replace`` 的重试包装。

    Windows 上杀毒软件 / 搜索索引 / 同步盘会短暂占用刚写完的临时文件，
    此时 ``os.replace`` 抛 ``PermissionError``；隔几十毫秒重试即可，避免
    演示现场偶发"存不下来"。
    """
    for index in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if index == attempts - 1:
                raise
            time.sleep(0.03 * (index + 1))


def _normalize_chats(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """把聊天记录整理成 ``{角色名: [{"role","content"}]}``。"""
    chats: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return chats
    for role, messages in raw.items():
        name = str(role).strip()
        if not name or not isinstance(messages, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            role_name = "assistant" if str(message.get("role")) == "assistant" else "user"
            item: dict[str, Any] = {"role": role_name, "content": content}
            if "ts" in message:
                item["ts"] = message.get("ts")
            if "emotion" in message:
                item["emotion"] = message.get("emotion")
            cleaned.append(item)
        chats[name] = cleaned
    return chats


def _facts_from_legacy_memories(raw: Any, now: float,
                                default_role: str | None = None) -> dict[str, list[Fact]]:
    """v2 的 ``memories`` → 每角色一组长期 Fact（保守：全归「近况」）。

    兼容两种写法：``{角色: [str]}`` 与更早的全局列表 ``[str]``（此时挂到
    ``default_role``——v2 只有单角色，迁移后不该丢这些记忆）。
    """
    result: dict[str, list[Fact]] = {}
    if isinstance(raw, list):
        raw = {default_role: raw} if default_role else {}
    if not isinstance(raw, dict):
        return result
    for role, items in raw.items():
        name = str(role).strip()
        if not name or not isinstance(items, list):
            continue
        facts: list[Fact] = []
        for item in items:
            text = item.get("content") if isinstance(item, dict) else item
            if not isinstance(text, str) or not text.strip():
                continue
            facts.append(
                Fact(
                    content=text.strip(),
                    category="近况",
                    confidence=0.5,
                    first_seen=now,
                    last_confirmed=now,
                    origin="working",
                )
            )
        if facts:
            result[name] = facts
    return result


def _seed_turns_from_chats(messages: list[dict[str, Any]], now: float, limit: int = 20):
    """旧格式迁移时用聊天记录预热短期层，保证迁移后立刻「记得」最近几轮。"""
    from memory_os.schema import WorkingTurn

    recent = messages[-limit:]
    turns: list[WorkingTurn] = []
    for message in recent:
        turns.append(
            WorkingTurn(
                role=message.get("role", "user"),
                content=message.get("content", ""),
                ts=now,
                emotion=message.get("emotion"),
            )
        )
    return turns


def normalize_data(raw: Any) -> dict[str, Any]:
    """把任意版本/任意脏数据整理成合法的 v3 结构（不抛异常）。"""
    now = now_ms()
    if not isinstance(raw, dict):
        return default_data()

    data = default_data()
    data["chats"] = _normalize_chats(raw.get("chats"))
    data["meta"] = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    legacy_facts = _facts_from_legacy_memories(
        raw.get("memories"), now, default_role=next(iter(data["chats"]), None)
    )

    roles_raw = raw.get("roles")
    if isinstance(roles_raw, dict):
        for role, payload in roles_raw.items():
            name = str(role).strip()
            if not name:
                continue
            memory = RoleMemory.from_dict(payload)
            if not memory.facts and name in legacy_facts:
                memory.facts = legacy_facts.pop(name)
            data["roles"][name] = memory.to_dict()
    else:
        # 旧平铺格式：顶层键就是角色名，值为消息列表
        for role, payload in raw.items():
            if role in {"version", "chats", "roles", "shared_facts", "meta", "memories"}:
                continue
            if not isinstance(payload, list):
                continue
            name = str(role).strip()
            if not name:
                continue
            data["chats"].setdefault(name, _normalize_chats({name: payload}).get(name, []))
            memory = RoleMemory.from_dict({})
            memory.turns = _seed_turns_from_chats(data["chats"].get(name, []), now)
            memory.turn_counter = sum(1 for turn in memory.turns if turn.role == "user")
            memory.relationship.turn_count = memory.turn_counter
            memory.relationship.updated_at = now
            if name in legacy_facts:
                memory.facts = legacy_facts.pop(name)
            data["roles"][name] = memory.to_dict()

    # 兜底：chats 里有、roles 里没有的角色补一个记忆（v2 迁移时把最近对话预热成短期层）
    for role, messages in data["chats"].items():
        if role in data["roles"]:
            continue
        memory = RoleMemory.from_dict({})
        memory.turns = _seed_turns_from_chats(messages, now)
        memory.turn_counter = sum(1 for turn in memory.turns if turn.role == "user")
        memory.relationship.turn_count = memory.turn_counter
        memory.relationship.updated_at = now
        data["roles"][role] = memory.to_dict()

    # v2 遗留 memories 里若有孤儿角色，也保留成角色记忆（已存在的角色只补 facts，不覆盖短期层）
    for role, facts in legacy_facts.items():
        existing = data["roles"].get(role)
        memory = RoleMemory.from_dict(existing) if isinstance(existing, dict) else RoleMemory.from_dict({})
        memory.facts = facts
        data["roles"][role] = memory.to_dict()

    shared_raw = raw.get("shared_facts")
    shared: list[Fact] = []
    if isinstance(shared_raw, list):
        for item in shared_raw:
            if isinstance(item, dict):
                fact = Fact.from_dict(item)
                fact.scope = "shared"
                shared.append(fact)
    data["shared_facts"] = [fact.to_dict() for fact in shared]
    data["version"] = STORAGE_VERSION
    return data


class MemoryStore:
    """``data/memory.json`` 的读写门面（唯一允许碰文件的地方）。"""

    def __init__(self, path: Path) -> None:
        """记录记忆文件路径（不立即读盘）。"""
        self.path = Path(path)

    # -- 读 -----------------------------------------------------------------
    def load(self) -> dict:
        """读取记忆文件；缺文件/空文件/破损 JSON → 默认结构并保留 ``.bak``。"""
        if not self.path.exists():
            return default_data()
        try:
            text = self.path.read_text(encoding="utf-8")
            raw = json.loads(text) if text.strip() else None
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            self._quarantine()
            return default_data()
        if not isinstance(raw, dict):
            self._quarantine()
            return default_data()
        return normalize_data(raw)

    def _quarantine(self) -> None:
        """把无法解析的文件重命名成 ``*.bak``（``memory.json`` → ``memory.bak``）。"""
        try:
            if self.path.exists():
                self.path.replace(self.path.with_suffix(".bak"))
        except OSError:
            pass

    # -- 写 -----------------------------------------------------------------
    def save(self, data: dict) -> None:
        """原子写入（tmp + ``os.replace``），始终写入 ``version=3``。"""
        payload = dict(data) if isinstance(data, dict) else default_data()
        payload["version"] = STORAGE_VERSION
        for key, value in default_data().items():
            payload.setdefault(key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_retry(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def backup(self, data: dict) -> Path | None:
        """把当前记忆另存成 ``*.backup.json``；失败返回 None。"""
        target = self.path.with_suffix(".backup.json")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(data) if isinstance(data, dict) else default_data()
            payload["version"] = STORAGE_VERSION
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            return None
        return target


__all__ = ["MemoryStore", "STORAGE_VERSION", "normalize_data"]
