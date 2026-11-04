"""全局配置与常量：路径、模型、三层记忆的超参数。

本模块是全项目唯一的「魔法数字」来源。所有可调参数集中在此，
`MemoryConfig` 允许测试与演示现场临时覆盖而不改动全局常量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
DOCS_DIR = APP_DIR / "docs"
MEMORY_FILE = DATA_DIR / "memory.json"
CUSTOM_ROLES_FILE = DATA_DIR / "custom_roles.json"
ARCHIVE_DIR = DATA_DIR / "archive"   # L3 固化快照落盘目录（data/archive/<角色>/<id>.json）
SETTINGS_FILE = DATA_DIR / "settings.json"   # 界面偏好：我的头像、是否允许 TA 主动开口
AVATAR_DIR = DATA_DIR / "avatars"    # 用户上传的头像原图（本地保存，不上传任何地方）
AVATAR_PX = 96                       # 渲染时压到这个边长，base64 直接塞进气泡 HTML
AVATAR_MAX_BYTES = 4 * 1024 * 1024   # 单张上限：手机照片也够用，超过就拒绝

# 存储版本：v4 在 v3 基础上加了 L3 快照索引与 L4 自我认知
MEMORY_VERSION = 4

# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
MAX_CONTEXT_MESSAGES = 20  # 主对话回放的历史条数上限

# ---------------------------------------------------------------------------
# 情绪
# ---------------------------------------------------------------------------

ALLOWED_EMOTIONS = ("开心", "难过", "生气", "平静")
NEGATIVE_EMOTIONS = frozenset({"难过", "生气"})
EMOTION_ALIASES = {
    "高兴": "开心",
    "快乐": "开心",
    "愉快": "开心",
    "兴奋": "开心",
    "伤心": "难过",
    "悲伤": "难过",
    "失落": "难过",
    "委屈": "难过",
    "愤怒": "生气",
    "恼火": "生气",
    "焦虑": "难过",
    "中性": "平静",
    "平淡": "平静",
}
EMOTION_EMOJI = {"开心": "😄", "难过": "😢", "生气": "😠", "平静": "😌"}
EMOTION_COLOR = {"开心": "#f6c344", "难过": "#5b8ff9", "生气": "#f4664a", "平静": "#8c8c8c"}

# 情绪强度（用于短期记忆 salience 与情景记忆 arousal 的兜底估算）
EMOTION_INTENSITY = {"开心": 0.7, "难过": 0.8, "生气": 0.85, "平静": 0.35}

# ---------------------------------------------------------------------------
# 三层记忆超参数
# ---------------------------------------------------------------------------

SHORT_TERM_MAX_TURNS = 20      # 短期层最多保留多少条 turn（user+assistant 各算一条）
SHORT_TERM_INJECT_TURNS = 8    # 注入 Prompt 时逐字回放的最近条数
CONSOLIDATE_EVERY_TURNS = 4    # 每 N 个用户轮触发一次「记忆固化」
CONSOLIDATE_MIN_TURNS = 3      # 少于这么多条新 turn 不固化

EPISODE_TOP_K = 4              # 中期层注入条数
FACT_TOP_K = 8                 # 长期层注入条数
LOOP_MAX = 3                   # 未闭环事件注入条数

W_SIM = 0.45                   # 语义相似度权重
W_IMP = 0.20                   # 重要性权重
W_REC = 0.20                   # 时间新鲜度权重
W_EMO = 0.15                   # 情绪显著性权重

EPISODE_TAU_DAYS = 10.0        # 情景记忆遗忘时间常数（天）
FACT_TAU_DAYS = 30.0           # 语义记忆遗忘时间常数（天）
ARCHIVE_THRESHOLD = 0.15       # 强度低于此值进入「淡化层」
RECALL_STRENGTH_BOOST = 0.10   # 每次被召回后的强化量
RECALL_COUNT_BONUS = 0.12      # recall_count 对强度的乘性加成

MEMORY_BLOCK_BUDGET = 1800     # 记忆段落目标字符数

# 记忆冲突消解阈值
FACT_MERGE_SIM = 0.82          # 高于此值视为同一条事实
FACT_CONFLICT_SIM = 0.50       # 高于此值且语义矛盾 → 记为「记忆修正」
LOOP_MERGE_SIM = 0.80

CATEGORIES = ("身份", "喜好", "厌恶", "重要人物", "长期目标", "近况", "禁忌", "约定")
FACT_CATEGORY_WEIGHT = {
    "身份": 1.0,
    "禁忌": 1.0,
    "约定": 1.0,
    "长期目标": 0.9,
    "重要人物": 0.9,
    "喜好": 0.8,
    "厌恶": 0.8,
    "近况": 0.6,
}
FACT_PIN_CATEGORIES = frozenset({"身份", "禁忌", "约定"})
FACT_PIN_CONFIDENCE = 0.7
FACT_PIN_MAX = 2
FACT_SHARED_CATEGORIES = frozenset({"身份", "重要人物", "长期目标", "禁忌"})

# 关系阶段阈值（左闭右开，单位为亲密度 0..100）
STAGE_THRESHOLDS = ((20.0, "陌生"), (45.0, "熟悉"), (75.0, "亲近"), (101.0, "亲密"))
INTIMACY_PER_TURN = 0.8
INTIMACY_CONSOLE_BONUS = 0.6   # 用户负面情绪 + 角色回应 → 额外亲密
CANONICAL_STAGES = ("陌生", "熟悉", "亲近", "亲密")

# 防翻旧账（mood gate）阈值
MOOD_GATE_VALENCE = -0.3
MOOD_GATE_AROUSAL = 0.6
MOOD_GATE_SIM = 0.25
# 信任越高，越敢碰旧伤：门槛最多放宽 30%（信任 0 → 0.25；信任 100 → 0.175）
TRUST_GATE_RELAX = 0.30

# 每层消融开关的显示名（UI 用）
TIER_LABELS = {"short": "短期·工作记忆", "mid": "中期·情景记忆", "long": "长期·语义画像"}
ALL_TIERS = frozenset({"short", "mid", "long"})

# ---------------------------------------------------------------------------
# 五层记忆扩展（L0 原文库 / L3 固化快照 / L4 自我认知）
# ---------------------------------------------------------------------------

ARCHIVE_TOP_K = 5              # L0 原文库检索返回条数
SELF_NOTE_MAX = 40             # L4 自我认知最多保留多少条
SELF_NOTE_MERGE_SIM = 0.80     # 相似度高于此值视为同一条自我叙述（只刷新时间/次数）
SELF_NOTE_INJECT_MAX = 3       # 注入 Prompt 时最多带几条（只在会话首轮）
SNAPSHOT_MAX = 30              # L3 快照索引最多保留多少条
CORRECTION_CONFIDENCE = 0.90   # 用户亲手纠正的事实，置信度取高值

# ---------------------------------------------------------------------------
# 人设编译器（Persona Forge）
# ---------------------------------------------------------------------------

PERSONA_PROBE_QUESTIONS = (
    "我今天有点撑不住了，什么都不想干。",
    "你要是能变成人，最想和我一起做什么？",
    "我最讨厌别人骗我了，你会骗我吗？",
)
PERSONA_PASS_SCORE = 75
PERSONA_KNOBS = ("毒舌", "温柔", "话痨", "神秘")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    """把数值夹在 [low, high] 区间内。"""
    return max(low, min(high, value))


def stage_of(intimacy: float) -> str:
    """按亲密度换算关系阶段（陌生 / 熟悉 / 亲近 / 亲密）。"""
    for upper, name in STAGE_THRESHOLDS:
        if intimacy < upper:
            return name
    return CANONICAL_STAGES[-1]


@dataclass
class MemoryConfig:
    """三层记忆的可调参数快照；测试与演示可通过它局部覆盖全局常量。"""

    short_term_max_turns: int = SHORT_TERM_MAX_TURNS
    short_term_inject_turns: int = SHORT_TERM_INJECT_TURNS
    consolidate_every_turns: int = CONSOLIDATE_EVERY_TURNS
    consolidate_min_turns: int = CONSOLIDATE_MIN_TURNS
    episode_top_k: int = EPISODE_TOP_K
    fact_top_k: int = FACT_TOP_K
    loop_max: int = LOOP_MAX
    weights: dict[str, float] = field(
        default_factory=lambda: {"sim": W_SIM, "importance": W_IMP, "recency": W_REC, "emotion": W_EMO}
    )
    episode_tau_days: float = EPISODE_TAU_DAYS
    fact_tau_days: float = FACT_TAU_DAYS
    archive_threshold: float = ARCHIVE_THRESHOLD
    block_budget: int = MEMORY_BLOCK_BUDGET
    fact_merge_sim: float = FACT_MERGE_SIM
    fact_conflict_sim: float = FACT_CONFLICT_SIM
    loop_merge_sim: float = LOOP_MERGE_SIM
    intimacy_per_turn: float = INTIMACY_PER_TURN
    intimacy_console_bonus: float = INTIMACY_CONSOLE_BONUS
    # 消融开关（答辩现场可逐项关掉，观察回答质量变化）
    mood_gate: bool = True
    conflict_resolution: bool = True
    auto_consolidate: bool = True
    # 五层记忆扩展（L0 原文库 / L3 快照 / L4 自我认知）
    archive_top_k: int = ARCHIVE_TOP_K
    self_note_max: int = SELF_NOTE_MAX
    self_note_merge_sim: float = SELF_NOTE_MERGE_SIM
    self_note_inject_max: int = SELF_NOTE_INJECT_MAX
    snapshot_max: int = SNAPSHOT_MAX
    correction_confidence: float = CORRECTION_CONFIDENCE
