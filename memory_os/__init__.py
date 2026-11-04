"""memory_os：EmoMem 的记忆引擎包（五层记忆 · 检索 · 固化 · 遗忘 · 自我认知）。

对外只需要记住三个名字：:class:`MemoryEngine`（全部 API）、:class:`MemoryStore`（JSON 持久化）、
:class:`MemoryConfig`（可调参数，复用 ``core.config`` 的定义）。
五层口径见 SPEC §8.0：L0 原文库 / L1 会话层 / L2 长期事实 / L3 固化快照 / L4 自我认知。
"""

from __future__ import annotations

from core.config import MemoryConfig
from memory_os.consolidate import run_consolidation
from memory_os.decay import decay_curve, decay_pass, strength
from memory_os.engine import DEFAULT_OPENER, MemoryEngine, fact_snapshot_item, normalize_emotion
from memory_os.layers import archive_stats, search_archive
from memory_os.retrieval import format_time_label, render_block
from memory_os.schema import (
    CATEGORIES,
    ConsolidationResult,
    Episode,
    Fact,
    MemoryBundle,
    MemorySnapshot,
    MemoryTrace,
    OpenLoop,
    RelationshipState,
    RoleMemory,
    SelfNote,
    TraceItem,
    WorkingTurn,
    default_data,
    now_ms,
)
from memory_os.store import MemoryStore, normalize_data
from memory_os.textsim import cosine, similarity, term_frequencies

__version__ = "1.1.0"

__all__ = [
    "CATEGORIES",
    "ConsolidationResult",
    "DEFAULT_OPENER",
    "Episode",
    "Fact",
    "MemoryBundle",
    "MemoryConfig",
    "MemoryEngine",
    "MemorySnapshot",
    "MemoryStore",
    "MemoryTrace",
    "OpenLoop",
    "RelationshipState",
    "RoleMemory",
    "SelfNote",
    "TraceItem",
    "WorkingTurn",
    "__version__",
    "archive_stats",
    "cosine",
    "decay_curve",
    "decay_pass",
    "default_data",
    "fact_snapshot_item",
    "format_time_label",
    "normalize_data",
    "normalize_emotion",
    "now_ms",
    "render_block",
    "run_consolidation",
    "search_archive",
    "similarity",
    "strength",
    "term_frequencies",
]
