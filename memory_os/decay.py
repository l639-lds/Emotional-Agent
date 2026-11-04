"""遗忘曲线、间隔重复强化与「淡化层」标记（SPEC §3.3）。

公式（可复算）：
``strength = clamp(0, 1, base_strength * exp(-Δt_days/τ) * (1 + 0.12 * recall_count))``

- ``τ``：Episode 10 天、Fact 30 天（:class:`core.config.MemoryConfig` 可覆盖）；
- ``Δt``：从 ``last_recalled_at`` / ``last_confirmed`` / ``created_at`` 起算；
- 被检索命中时 :func:`reinforce` 做一次间隔重复强化：
  ``recall_count += 1``、``last_recalled_at = now``、``base_strength = min(1, +0.1)``；
- 强度低于 ``ARCHIVE_THRESHOLD`` 只打 ``archived=True``，**不删除文件内容**（UI 标灰）。
"""

from __future__ import annotations

import math
from typing import Iterable

from core.config import (
    ARCHIVE_THRESHOLD,
    EPISODE_TAU_DAYS,
    FACT_TAU_DAYS,
    RECALL_COUNT_BONUS,
    RECALL_STRENGTH_BOOST,
    clamp,
)
from memory_os.schema import Episode, Fact, as_ms, days_between

MemoryItem = Episode | Fact


def item_tau_days(item: MemoryItem, episode_tau: float = EPISODE_TAU_DAYS,
                  fact_tau: float = FACT_TAU_DAYS) -> float:
    """返回该条目使用的遗忘时间常数（Fact 更「耐用」）。"""
    return fact_tau if isinstance(item, Fact) else episode_tau


def last_touch(item: MemoryItem) -> float:
    """返回该条目最近一次「被确认/被召回/被创建」的毫秒时间戳。"""
    if isinstance(item, Fact):
        return item.last_confirmed or item.first_seen
    return item.last_recalled_at or item.created_at


def strength(item: MemoryItem, now: float | None = None, *,
             episode_tau: float = EPISODE_TAU_DAYS,
             fact_tau: float = FACT_TAU_DAYS) -> float:
    """按遗忘曲线计算条目在 ``now`` 时刻的记忆强度（0..1）。"""
    stamp = as_ms(now)
    tau = max(0.1, item_tau_days(item, episode_tau, fact_tau))
    delta_days = days_between(stamp, last_touch(item))
    decay = math.exp(-max(0.0, delta_days) / tau)
    bonus = 1.0 + RECALL_COUNT_BONUS * max(0, item.recall_count)
    return clamp(item.base_strength * decay * bonus, 0.0, 1.0)


def reinforce(item: MemoryItem, now: float | None = None) -> float:
    """检索命中后的间隔重复强化，返回强化后的强度。"""
    stamp = as_ms(now)
    item.recall_count = max(0, item.recall_count) + 1
    item.base_strength = min(1.0, item.base_strength + RECALL_STRENGTH_BOOST)
    if isinstance(item, Fact):
        item.last_confirmed = stamp
    else:
        item.last_recalled_at = stamp
    return strength(item, stamp)


def mark_archived(items: Iterable[MemoryItem], now: float | None = None,
                  threshold: float = ARCHIVE_THRESHOLD, *,
                  episode_tau: float = EPISODE_TAU_DAYS,
                  fact_tau: float = FACT_TAU_DAYS) -> int:
    """把强度低于阈值的条目标为 ``archived``（只标记不删除），返回新归档条数。"""
    stamp = as_ms(now)
    newly = 0
    for item in items:
        if item.archived:
            continue
        if strength(item, stamp, episode_tau=episode_tau, fact_tau=fact_tau) < threshold:
            item.archived = True
            newly += 1
    return newly


def decay_pass(items: Iterable[MemoryItem], now: float | None = None,
               threshold: float = ARCHIVE_THRESHOLD, *,
               episode_tau: float = EPISODE_TAU_DAYS,
               fact_tau: float = FACT_TAU_DAYS) -> dict:
    """跑一次遗忘：只标记归档，返回 ``{"archived": n, "weakest": 最弱强度}``。"""
    materialized = list(items)
    stamp = as_ms(now)
    archived = mark_archived(
        materialized, stamp, threshold, episode_tau=episode_tau, fact_tau=fact_tau
    )
    weakest = 1.0
    for item in materialized:
        weakest = min(
            weakest, strength(item, stamp, episode_tau=episode_tau, fact_tau=fact_tau)
        )
    return {"archived": archived, "weakest": round(weakest, 4)}


def average_strength(items: Iterable[MemoryItem], now: float | None = None, *,
                     only_active: bool = True,
                     episode_tau: float = EPISODE_TAU_DAYS,
                     fact_tau: float = FACT_TAU_DAYS) -> float:
    """计算一组条目的平均强度（默认跳过已归档/已被取代的条目）。"""
    stamp = as_ms(now)
    values: list[float] = []
    for item in items:
        if only_active and (item.archived or getattr(item, "superseded_by", None)):
            continue
        values.append(
            strength(item, stamp, episode_tau=episode_tau, fact_tau=fact_tau)
        )
    if not values:
        return 0.0
    return sum(values) / len(values)


def _future_strength(item: MemoryItem, now: float, days: float, *,
                     episode_tau: float, fact_tau: float) -> float:
    """预测条目在未来第 ``days`` 天的强度（同一公式外推）。"""
    stamp = as_ms(now) + days * 86_400_000.0
    return strength(item, stamp, episode_tau=episode_tau, fact_tau=fact_tau)


def decay_curve(episodes: Iterable[Episode], facts: Iterable[Fact],
                now: float | None = None, days: int = 14, *,
                episode_tau: float = EPISODE_TAU_DAYS,
                fact_tau: float = FACT_TAU_DAYS) -> list[dict]:
    """外推未来 ``days`` 天的平均强度，供 UI 折线图。

    Returns:
        ``[{"label": "今天", "episode": 0.82, "fact": 0.94}, ...]``，共 ``days`` 个点。
    """
    stamp = as_ms(now)
    live_episodes = [item for item in episodes if not item.archived]
    live_facts = [item for item in facts if item.archived is False and not item.superseded_by]
    curve: list[dict] = []
    for offset in range(max(1, days)):
        label = "今天" if offset == 0 else f"+{offset}天"
        episode_values = [
            _future_strength(item, stamp, offset, episode_tau=episode_tau, fact_tau=fact_tau)
            for item in live_episodes
        ]
        fact_values = [
            _future_strength(item, stamp, offset, episode_tau=episode_tau, fact_tau=fact_tau)
            for item in live_facts
        ]
        curve.append(
            {
                "label": label,
                "episode": round(sum(episode_values) / len(episode_values), 4)
                if episode_values
                else 0.0,
                "fact": round(sum(fact_values) / len(fact_values), 4) if fact_values else 0.0,
            }
        )
    return curve


__all__ = [
    "average_strength",
    "decay_curve",
    "decay_pass",
    "item_tau_days",
    "last_touch",
    "mark_archived",
    "reinforce",
    "strength",
]
