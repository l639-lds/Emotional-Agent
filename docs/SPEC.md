# EmoMem 改造规格书（内部契约，所有协作者共同遵循）

> 本文件是**接口契约**，不是用户文档。任何人不得单方面改动签名；确需改动先在任务板上留言并等 Lead 确认。

## 0. 项目定位

原有项目：Streamlit + DeepSeek 的情感陪伴 Agent（单文件 `app.py`，876 行，JSON 存储，v2 schema）。
本次改造目标：把「单一 `memories: list[str]`」升级为**情感感知的五层记忆系统 EmoMem**（v4：L0 原文库 / L1 会话层 / L2 长期事实 / L3 固化快照 / L4 自我认知），并把角色生成升级为**人设编译器 Persona Forge**。

对外卖点（海报/答辩用同一套表述，措辞不要各写一套）：

| # | 创新点 | 一句话 |
|---|---|---|
| 1 | 五层记忆结构 | L0 原文库（永不删可检索）→ L1 会话层（最近 20 + 摘要 + Episode）→ L2 长期事实 → L3 固化快照（每次整理留档可回溯）→ L4 自我认知（它对自己的叙述）；「前三层记的是你，L4 记的是它」 |
| 2 | 记忆固化 + 每 4 轮"睡眠整理" | 一次调用同时产出情景片段、事实、未闭环事件、称呼、关系增量、一条自我叙述，并写一份快照 |
| 3 | 情绪加权检索 + 防翻旧账 | 每条记忆带效价 valence/唤醒度 arousal，检索分 = 相似度×重要性×时间衰减×情绪显著性；用户情绪负面时抑制未被提及的高唤醒负性记忆 |
| 4 | 记忆冲突消解 | 新旧事实矛盾时旧条目 `superseded_by` 标记作废（保留审计），置信度随证据数演化 |
| 5 | 可解释 Trace + 记忆主权 | 每轮记录注入了哪些记忆、分值构成与理由、未采用的记忆及原因；每条记忆还能「忘掉」或「改正」（改正走 `superseded_by` 留档） |
| 6 | 关系档案驱动人格漂移 | intimacy/trust/stage/称呼演化/共同梗，阶段反向调节语气（陌生克制 → 亲密可调侃撒娇），人设不再静态 |
| 7 | 未闭环事件 + 主动关怀 | 从对话抽取"待跟进事项"（面试/体检/约定），下次主动追问；另有消融开关（现场对比关掉某层记忆的回复差异） |
| 8 | 人设编译器 Persona Forge | 一句描述 → 结构化人设卡（语音指纹/情绪反应表/关系弧/边界）→ 自评打分 → 定向重写 → 3 问人设体检 |

## 1. 目录与文件归属（写作用域，禁止越界）

```
app.py                      Lead        Streamlit UI（唯一入口）
core/config.py              Lead        常量/路径/特征开关（所有人只读引用）
core/llm.py                 Lead        LLM 调用与 JSON 解析兜底；call_json 为唯一 LLM 入口
core/demo.py                Lead        离线演示模式的假模型（按 purpose 分发）
core/__init__.py            Lead
memory_os/__init__.py       mem-core    公开导出
memory_os/schema.py         mem-core    数据类 + dict 互转 + 校验
memory_os/store.py          mem-core    原子读写 / v2→v3 迁移 / 角色隔离
memory_os/textsim.py        mem-core    纯 Python 字符 n-gram TF-IDF 余弦相似度
memory_os/retrieval.py      mem-core    打分、选层、mood gate、Trace 组装
memory_os/decay.py          mem-core    遗忘曲线 / 强化 / 归档
memory_os/consolidate.py    mem-core    固化：Episode 生成、Fact 合并/冲突、OpenLoop、关系更新
memory_os/layers.py         mem-core    L0 原文检索 / L3 快照落盘 / L4 自我认知的纯函数层（v4 新增）
memory_os/engine.py         mem-core    MemoryEngine 门面（写路径 + 读路径）
memory_os/prompts.py        mem-core    抽取/固化/主动关怀的 Prompt 模板
tests/test_memory_os.py     mem-core    pytest，全部离线、确定性
scripts/eval_memory.py      mem-core    离线评测，输出指标表（答辩/海报数字来源）
persona_forge/__init__.py   persona    公开导出
persona_forge/schema.py     persona    PersonaCard 等数据类 + 校验 + dict 互转
persona_forge/prompts.py    persona    元 Prompt（生成/自评/重写/体检）★本次重点
persona_forge/compiler.py   persona    生成→自评→重写→体检 编排
persona_forge/healthcheck.py persona   3 问人设体检（LLM 探针 + 离线规则兜底）
persona_forge/render.py     persona    渲染人设 Prompt 段落 + 关系阶段漂移指令
persona_forge/interview.py  Lead       人格教练：五问元 Prompt + 回答收束成人设速写（v4 新增）
persona_forge/legacy.py     persona    v1 旧角色（persona/opening 字符串）兼容包装
tests/test_persona_forge.py persona    pytest，全部离线、确定性
tests/test_interview.py     Lead       人格教练的状态机 / 收束 / 降级测试
ui/state.py                 Lead       会话状态、角色文件、引擎装配、两段式单轮（begin/finish）
ui/chat.py                  Lead       对话气泡渲染（自己靠右、TA 靠左，HTML 转义后自绘）
ui/panels.py                Lead       五层面板 / Trace / 关系 / 消融 / 记忆纠正
ui/forge.py                 Lead       锻造炉对话框 + 人格教练整页
tests/test_integration.py   Lead       跨模块契约（记忆 ↔ 人设 ↔ UI 状态层）
tests/test_app.py           Lead       Streamlit AppTest 端到端（含按钮点击链路）
docs/poster/*               poster     A2 海报（html/pdf/png）
docs/答辩稿.md / .docx        poster     一页答辩稿
```

## 2. 数据 Schema（`data/memory.json` v3 基础字段；v4 追加字段见 §8.1）

顶层：`{"version": 3, "chats": {...}, "roles": {角色名: RoleMemory}, "shared_facts": [Fact], "meta": {...}}`

- `chats`：沿用 v2（角色名 → `[{role, content}]`），UI 仍按它渲染气泡。
- `roles[角色名]`（`RoleMemory`）字段及默认值：

```python
turns: list[WorkingTurn]          # 短期记忆，仅保留最新 SHORT_TERM_MAX_TURNS 条
working_summary: str = ""         # 本会话滚动摘要（短期层的"压缩视图"）
summary_until_turn: int = 0       # working_summary 覆盖到第几条 turn（用于增量更新）
episodes: list[Episode]           # 中期记忆
facts: list[Fact]                 # 长期记忆（本角色私有）
open_loops: list[OpenLoop]
relationship: RelationshipState
last_consolidated_turn: int = 0   # 已固化到第几条 turn
turn_counter: int = 0             # 累计轮次（不因裁剪而减少）
```

- `shared_facts`：跨角色共享的**用户客观信息**（身份/重要人物/长期目标/禁忌）；`scope="shared"` 的 Fact 写这里，任何角色都能读到但只有产生它的角色可写。`scope="private"` 的写 `roles[x].facts`。
- 每条记录都必须有稳定 `id`（`f"{prefix}_{uuid4().hex[:10]}"`）与毫秒时间戳（float）。

### 2.1 数据类（`memory_os/schema.py`，字段名/类型不得改动）

```python
WorkingTurn:
    role: str            # "user" | "assistant"
    content: str
    ts: float
    emotion: str | None = None       # 该轮用户的情绪（assistant 轮为 None）
    salience: float = 0.5            # 0..1，写入时按情绪强度/信息量估算

Episode:                             # 中期记忆
    id: str; title: str; summary: str
    topics: list[str]                # 3-6 个话题词
    keywords: list[str]              # 用于检索的词（含 topics 与专名）
    valence: float                   # -1..1 效价
    arousal: float                   # 0..1 唤醒度
    importance: float                # 0..1
    turn_start: int; turn_end: int
    created_at: float; last_recalled_at: float = createdAt
    recall_count: int = 0
    base_strength: float = 1.0       # 记忆强度初值
    archived: bool = False

Fact:                                # 长期记忆
    id: str; content: str            # 一句话，第三人称，含"用户"
    category: str                    # 见 CATEGORIES
    confidence: float                # 0..1
    evidence_count: int = 1
    first_seen: float; last_confirmed: float
    scope: str = "private"           # "private" | "shared"
    superseded_by: str | None = None # 被哪条新事实取代
    recall_count: int = 0
    base_strength: float = 1.0
    archived: bool = False
    origin: str = "working"          # "working" | "episode:<id>"

OpenLoop:                            # 未闭环事件
    id: str; what: str               # "下周三去面试"（陈述句，可直接提问）
    kind: str                        # "待办" | "承诺" | "悬念" | "情绪牵挂"
    due_hint: str | None = None      # "下周三" 之类的时间线索，可为 None
    status: str = "open"             # "open" | "closed"
    created_at: float; closed_at: float | None = None
    mention_count: int = 1

RelationshipState:
    intimacy: float = 0.0            # 0..100（亲密度）
    trust: float = 0.0               # 0..100
    stage: str = "陌生"              # 由 intimacy 推导：<20 陌生 / <45 熟悉 / <75 亲近 / else 亲密
    nickname: str | None = None      # 角色当前对用户的称呼
    nickname_history: list[dict]     # [{"value": str, "at": float}]
    inside_jokes: list[str]          # 只有彼此懂的梗（≤5）
    shared_themes: list[str]         # 反复出现的话题（≤8）
    turn_count: int = 0
    updated_at: float = 0.0

TraceItem:                           # 可解释记忆的单条
    tier: str                        # "short" | "mid" | "long"
    kind: str                        # "turn" | "episode" | "fact" | "loop" | "relationship"
    id: str; text: str
    score: float
    components: dict[str, float]     # {"sim":, "importance":, "recency":, "emotion":}
    reason: str                      # 中文理由，如"与'面试'高度相关，且是高唤醒负面记忆"
    pinned: bool = False

MemoryTrace:
    items: list[TraceItem]
    skipped: list[TraceItem]         # 被 mood gate / 预算裁掉的（带 reason）
    notes: list[str]                 # ["抑制 1 条高唤醒负性记忆（防翻旧账）"]
    budget: int = 0                  # 注入字符数
```

`CATEGORIES = ("身份", "喜好", "厌恶", "重要人物", "长期目标", "近况", "禁忌", "约定")`，未知归 `"近况"`。

## 3. `memory_os` 公开 API（`memory_os/__init__.py` 导出这些名字）

```python
class MemoryStore:
    def __init__(self, path: Path) -> None
    def load(self) -> dict                    # 缺文件/损坏 → 默认结构；自动迁移 v2
    def save(self, data: dict) -> None        # 原子写（tmp + replace），保留 version=3
    def backup(self, data: dict) -> Path | None   # 可选，写 data/*.backup.json

class MemoryEngine:
    def __init__(self, store: MemoryStore, *, llm: JsonCaller | None = None,
                 config: "MemoryConfig | None" = None) -> None
    # llm 默认 core.llm.call_json；测试注入 fake

    # 写路径
    def record_turn(self, role: str, user_text: str, assistant_text: str,
                    emotion: str, *, now: float | None = None) -> None
        # 追加两条 WorkingTurn、更新 RelationshipState 计数与亲密度、裁剪短期层、
        # 每 CONSOLIDATE_EVERY_TURNS 个 user 轮触发 consolidate()（失败不影响本轮回复）
    def consolidate(self, role: str, *, force: bool = False,
                    now: float | None = None) -> ConsolidationResult
    def close_loop(self, role: str, loop_id: str, *, now: float | None = None) -> bool
    def reset_role(self, role: str) -> None
    def decay_pass(self, role: str, *, now: float | None = None) -> dict   # 返回 {archived: n, weakest: float}

    # 读路径
    def retrieve(self, role: str, query: str, emotion: str = "平静", *,
                 tiers: set[str] | None = None,       # 消融开关：默认 {"short","mid","long"}
                 now: float | None = None) -> MemoryBundle
    def build_memory_block(self, bundle: MemoryBundle) -> str   # 渲染成 System Prompt 段落
    def snapshot_for_ui(self, role: str, *, now: float | None = None) -> dict
        # 键：short/mid/long/relationship/loops/stats/decay_curve，详见 §6
    def proactive_openers(self, role: str, *, limit: int = 3,
                          now: float | None = None) -> list[str]
    def stats(self, role: str) -> dict       # 各层条数、已归档数、平均强度
```

`ConsolidationResult`（dataclass）：`done: bool`、`episode_id: str | None`、`new_facts: list[str]`、
`updated_facts: list[str]`、`superseded: list[dict]`（`{"old":..., "new":..., "reason":...}`）、
`loops_opened: list[str]`、`loops_closed: list[str]`、`relationship: dict`（前后）、`notes: list[str]`、`used_llm: bool`。

`MemoryBundle`（dataclass）：`role`、`relationship`、`facts: list[TraceItem]`、`episodes: list[TraceItem]`、
`loops: list[OpenLoop]`、`working_turns: list[WorkingTurn]`、`working_summary`、
`trace: MemoryTrace`、`style_directives: list[str]`、`block: str`。

### 3.1 检索算法（必须可按此复算，测试会断言）

```
sim      = cosine(tfidf(query), tfidf(item.text))          # 纯 Python，字符 2-gram，见 textsim
recency  = exp(-Δt_days / τ)                               # Episode τ=10 天，Fact τ=30 天；用 last_recalled/last_confirmed
emotion  = 0.5*abs(valence) + 0.5*arousal                  # Fact 无该字段时用 0.4 常数
score    = W_SIM*sim + W_IMP*importance + W_REC*recency + W_EMO*emotion
# 权重：W_SIM=0.45 W_IMP=0.20 W_REC=0.20 W_EMO=0.15（在 core/config.py）
# Fact 的 importance = confidence * 类别权重（禁忌/约定/身份=1.0，长期目标/重要人物=0.9，喜好/厌恶=0.8，近况=0.6）
# Fact 的 score 额外 +0.12 若 category in {"禁忌","约定"}
```

Mood gate（防翻旧账）：当 `emotion in {"难过","生气"}` 且 `item.valence < -0.3 and item.arousal > 0.6 and sim < 0.25`
→ 不注入，记入 `trace.skipped`，`notes` 追加一条说明。
门槛可由信任放宽：`threshold = 0.25 * (1 - TRUST_GATE_RELAX * trust/100)`（`TRUST_GATE_RELAX=0.30`，
信任 100 时 0.175）——`RelationshipState.trust` 的唯一决策用途。
Pinned（必注入，仍计入 trace）：`confidence >= 0.7` 且 `category in {"身份","禁忌","约定"}` 的 Fact 最多 2 条；
未闭环的 OpenLoop 全量（≤3 条，`due_hint` 非空优先）。

预算：`build_memory_block` 目标 ≤ 1800 字符；超预算按 score 升序裁剪并写 `trace.notes`。

### 3.2 固化算法（`consolidate`）

1. 取 `turns[last_consolidated_turn:]`；若 `force=False` 且不足 `CONSOLIDATE_MIN_TURNS*2` 条 → 返回 `done=False`。
2. **一次** LLM 调用（`purpose="consolidate"`），System Prompt 见 `prompts.py`，要求返回：
```json
{"episode": {"title": "...", "summary": "...", "topics": ["..."], "valence": -0.4, "arousal": 0.7,
             "importance": 0.8, "keywords": ["..."]},
 "facts": [{"content": "用户在找实习", "category": "近况", "confidence": 0.8, "scope": "private"}],
 "loops_open": [{"what": "下周三去面试", "kind": "待办", "due_hint": "下周三"}],
 "loops_close": ["<已有 loop id>"],
 "nickname": "小明" | null,
 "relationship": {"intimacy_delta": 2, "trust_delta": 1, "themes": ["求职"], "inside_joke": null}}
```
3. 校验/兜底：字段缺失或类型错误逐项降级（不整条丢弃）；LLM 抛错 → 走 `prompts.heuristic_extract()` 正则兜底，`used_llm=False`，并在 `notes` 说明。
4. Fact 写入规则（顺序执行）：
   - 现有 fact 与新 fact 的 `sim > 0.82` 且同 category → 视为同一条：`evidence_count += 1`、`last_confirmed = now`、`confidence = min(1, c + 0.1)`；内容更具体（更长）时用新文本替换。
   - `sim ∈ [0.5, 0.82]` 且检测到矛盾（`喜欢`/`不喜欢`、`讨厌`/`喜欢`、否定词差异、不同取值）→ 旧 fact `superseded_by = new.id`、新 fact `confidence = 0.75`，记入 `superseded`。
   - 其余 → 新增。
   - `importance` 高且被 ≥2 个 Episode 支撑的事实可 `scope` 提升为 `"shared"`（仅当 category ∈ {"身份","重要人物","长期目标","禁忌"}）。
5. OpenLoop：`loops_close` 里匹配到的置 `closed`；`loops_open` 与现有 open loop `sim>0.8` 视为同一件事 → `mention_count += 1`（不重复新增）。
6. 关系更新：`intimacy = clamp(0,100, intimacy + base + delta)`，`base = min(3, 0.8 * 新增 user 轮数)`；`trust += trust_delta`；`stage` 按阈值重算；`nickname` 变化时写 `nickname_history`。
7. `last_consolidated_turn = len(turns)`；`notes` 汇总人类可读的中文说明（用于 UI toast 与 Trace 面板）。

### 3.3 遗忘曲线

`strength(item, now) = clamp(0, 1, base_strength * exp(-Δt_days/τ) * (1 + 0.12*recall_count))`，
`Δ = now - (last_recalled_at | last_confirmed | created_at)`；被检索命中时 `recall_count += 1`、
`last_recalled_at = now`、`base_strength = min(1.0, base_strength + 0.1)`（间隔重复强化）。
`strength < ARCHIVE_THRESHOLD(0.15)` → `archived=True`（不再注入、文件保留、UI 标灰）。`decay_pass` 只做标记，不删除。

## 4. `core` 契约

```python
# core/config.py
APP_DIR, DATA_DIR, MEMORY_FILE, CUSTOM_ROLES_FILE, POSTER_DIR 等 Path
DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, MAX_CONTEXT_MESSAGES
ALLOWED_EMOTIONS, EMOTION_ALIASES, EMOTION_EMOJI, EMOTION_COLOR, NEGATIVE_EMOTIONS, EMOTION_INTENSITY
SHORT_TERM_MAX_TURNS=20, SHORT_TERM_INJECT_TURNS=8, CONSOLIDATE_EVERY_TURNS=4, CONSOLIDATE_MIN_TURNS=3
EPISODE_TOP_K=4, FACT_TOP_K=8, LOOP_MAX=3
W_SIM=0.45, W_IMP=0.20, W_REC=0.20, W_EMO=0.15
EPISODE_TAU_DAYS=10.0, FACT_TAU_DAYS=30.0, ARCHIVE_THRESHOLD=0.15
CATEGORIES=(...), FACT_CATEGORY_WEIGHT={...}, FACT_PIN_CATEGORIES, FACT_SHARED_CATEGORIES
STAGE_THRESHOLDS=((20,"陌生"),(45,"熟悉"),(75,"亲近"),(101,"亲密")), CANONICAL_STAGES
MEMORY_BLOCK_BUDGET=1800, MOOD_GATE_* 阈值, FACT_MERGE_SIM=0.82, FACT_CONFLICT_SIM=0.5, LOOP_MERGE_SIM=0.8
TIER_LABELS, ALL_TIERS, PERSONA_PROBE_QUESTIONS, PERSONA_PASS_SCORE, PERSONA_KNOBS
@dataclass MemoryConfig: 上述全部可调参数（字段名=小写常量名），测试用它局部覆盖
# 只读函数：stage_of(intimacy) -> str, clamp(v, lo, hi) -> float

# core/llm.py（已实现，可直接 import）
JsonCaller = Callable[..., dict[str, Any]]      # 关键字参数：temperature/max_tokens/purpose/demo_context
def call_json(system_prompt: str, user_prompt: str, *, temperature: float = 0.6,
              max_tokens: int = 1600, purpose: str = "json",
              demo_context: dict[str, Any] | None = None) -> dict[str, Any]
    # demo_context：仅离线演示模式使用，用来传入结构化上下文（如 {"turns": [...]}），
    # 让假模型能产出"像真的"数据；在线模式忽略该参数。请务必在调用时填入。
    # 唯一 LLM 入口。内部：demo 分流 → OpenAI JSON mode → 3 次重试（降 temperature + 去历史）→
    # 解析/修复（复用原 app.py 的 _extract_json_objects/_parse_json_object 逻辑）→ 失败抛 LLMError
class LLMError(RuntimeError)
def is_demo_mode() -> bool        # 环境变量 EMO_DEMO_MODE=1 或无 API Key 时自动 True
def configured() -> bool          # 是否有可用的 DEEPSEEK_API_KEY
def redact(text: str) -> str      # 打码 sk-*
def structured_chat(system_prompt: str, history: list[dict], history_note: str | None = None) -> dict
    # 主对话调用：保留原实现的 JSON mode / 3 次重试 / 历史 JSON 包装 / 最后一条只带 system+最后用户消息
```

`purpose` 取值（`core/demo.py` 按它分发假数据，任何人新增 purpose 必须同步 demo.py）：
`"consolidate"`、`"extract"`、`"proactive"`、`"persona_seed"`、`"persona_critique"`、`"persona_repair"`、`"persona_probe"`、`"chat"`。
引擎/编译器内部**所有** LLM 调用都必须带 `purpose=`。

### 4.1 demo_context 约定（离线演示模式的输入）

`core/demo.py` 已按下列键读取结构化上下文，实现方必须照此填充，否则演示模式会退化为空数据：

| purpose | demo_context 键 |
|---|---|
| `consolidate` | `turns`: `[{"role","content"}]`；可选 `fallback_summary`: str、`nickname`: str |
| `extract` | `turns`: 同上 |
| `proactive` | `open_loops`: `[{"what": ...}]` |
| `persona_seed` | `description`: str、`knobs`: dict |
| `persona_critique` | `card`: dict（`card_to_dict` 的结果） |
| `persona_repair` | `card`: dict、`description`: str |
| `persona_probe` | `question`: str |


## 5. `persona_forge` 公开 API

```python
# schema.py
@dataclass PersonaCard:
    name: str; tagline: str; avatar: str
    identity: dict[str, str]        # {"age_vibe","occupation","setting"}
    backstory: str
    values: list[str]
    speech: SpeechProfile           # 见下
    emotion_map: list[EmotionRule]  # {"trigger","reaction","body_language"} 3-5 条
    relationship_arc: dict[str, str]  # 键固定 "陌生"/"熟悉"/"亲近"/"亲密"
    boundaries: list[str]; anti_patterns: list[str]; memory_hooks: list[str]; taboos: list[str]
    legacy: bool = False
@dataclass SpeechProfile: register: str; avg_sentence_len: int; tics: list[str];
                          punctuation: str; banned_words: list[str]; sample_lines: list[str]  # 恰 3 条
@dataclass Critique: scores: dict[str, int]  # 6 维固定键：可执行性/一致性/差异化/情感深度/安全性/可演示性，各 0-100
                     issues: list[str]; verdict: str
@dataclass HealthReport: checks: list[dict]  # [{"name","ok","detail"}]
                         score: int; summary: str
@dataclass CompileResult: card: PersonaCard; critique: Critique; repair_rounds: int;
                          health: HealthReport; steps: list[str]; used_llm: bool

# compiler.py
def compile_persona(description: str, *, llm: JsonCaller | None = None,
                    knobs: dict[str, float] | None = None,
                    max_repair_rounds: int = 1) -> CompileResult
    # knobs: {"毒舌": 0..1, "温柔": 0..1, "话痨": 0..1, "神秘": 0..1} 缺省 0.5，参与 seed 与 repair prompt
    # 流程：seed → critique →（score<75 或 issues 非空）repair（≤max_repair_rounds 轮）→ healthcheck
    # 任一步 LLM 失败都要降级，绝不抛异常；最终必须返回一个合法 PersonaCard（必要时用描述兜底模板）
def card_to_dict(card) -> dict; dict_to_card(raw: dict) -> PersonaCard   # 校验+补默认值，不抛异常

# render.py
def render_persona_section(card: PersonaCard) -> str        # 【你是谁】【你怎么说话】…（含 3 条台词锚点）
def render_style_directives(card: PersonaCard, relationship: dict | None) -> list[str]
    # 按 stage 输出语气调节指令（人格漂移），relationship=None 时按"陌生"
def build_system_prompt(card, memory_block: str, stage: str, nickname: str | None) -> str
    # 三段式：人设 → 记忆 → 输出约束（JSON 协议），供 app.py 直接使用
def render_protocol_rules() -> str                          # 原 JSON_OUTPUT_RULES 的升级版（多了关系/主动性字段）

# legacy.py
def from_legacy(name: str, raw: dict) -> PersonaCard        # v1 {persona, opening, avatar, description} → legacy=True 的卡
```

JSON 协议升级（`render_protocol_rules`）：在保留 `reply/emotion/memory_update` 的基础上新增
`"initiative"`（本轮是否该主动追问，`null` 或一句追问）与 `"loop_hint"`（`null` 或"用户提到的新待办"），
解析层必须对旧模型输出的缺字段完全兼容。

## 6. UI 需要的 `snapshot_for_ui` 键（app.py 会严格按此读）

```python
{
 "short": {"turns": [{"role","content","ts_label","emotion"}...], "summary": str, "count": int},
 "mid":   {"items": [{"id","title","summary","topics","valence","arousal","importance",
                      "strength","recall_count","created_label","archived"}...]},
 "long":  {"items": [{"id","content","category","confidence","evidence_count","strength",
                      "last_confirmed_label","archived","superseded_by"}...],
           "shared": [ ... 同结构 ... ]},
 "relationship": {"intimacy","trust","stage","nickname","nickname_history","inside_jokes",
                  "shared_themes","turn_count"},
 "loops": [{"id","what","kind","due_hint","status","mention_count","created_label"}...],
 "stats": {"turns","episodes","facts","shared_facts","open_loops","archived","avg_strength"},
 "decay_curve": [{"label","episode","fact"}...]   # 未来 14 天强度预测，供折线图
}
```

## 7. 工程约束（所有人）

1. Python 3.10+ 语法；`from __future__ import annotations`；全部公开函数带类型标注与中文 docstring。
2. **不得新增第三方依赖**（只用标准库 + 已装的 streamlit/openai；测试用 pytest，已装）。
3. 所有网络访问只允许经 `core.llm.call_json` / `core.llm.structured_chat`。
4. 所有时间取值走参数 `now: float | None = None`（默认 `time.time()`），测试必须注入固定时间戳，禁止 `sleep`。
5. 单元测试必须离线、确定性、可在 3 秒内跑完；用 `FakeLLM` 类按 `purpose` 返回固定 JSON。
6. 不打印明文 API Key；异常信息经 `core.llm.redact`。
7. 中文注释/文案；变量名英文；单文件建议 ≤ 450 行，超了拆。
8. 不改动 `app.py`、`core/*`（除 Lead 授权）；不修改他人文件。写完自己跑 `python -m pytest tests/ -q`。
9. 复用原 `app.py` 里已验证的容错逻辑（JSON 提取、重试、脱敏），不要另起一套。

## 8. 五层记忆扩展（v4，本轮新增；对外口径见 §8.0）

### 8.0 五层定义（海报 / README / 答辩统一用这套说法，不得自创）

| 层 | 名字 | 内容 | 生命周期与注入策略 |
|---|---|---|---|
| L0 | 原文库 | 全量对话原文（`data["chats"][角色]`），永不删除，可按关键词检索 | 不注入；需要时用 `search_archive` 检索，带时间戳与出处 |
| L1 | 会话层 | 最近 20 轮原文 + 滚动摘要 + 每 4 轮固化的 Episode（情景记忆） | 每轮注入最近 8 条 + Top-K Episode |
| L2 | 长期事实 | 8 类事实，带置信度 / 证据数 / 类别权重 / 衰减 / 归档 | 每轮按分数注入，pinned 与门控见 §3.1 |
| L3 | 固化快照 | 每次固化的产物落盘（`data/archive/<角色>/<id>.json`）+ 索引，可回溯历史版本 | 不注入；UI 可查看 |
| L4 | 自我认知 | `selfNote`：它对自己的叙述（"我好像越来越会等你说完再回了"） | **只在新会话首轮 / 切换角色后首轮注入**，其余轮次不注入 |

> 设计理由（写进 README/答辩）：前三层记的都是"你"，L4 记的是"它"。前者是资料，后者才是性格的连续性。每轮都灌 = "别人每隔十秒告诉你你是谁"，那不是成长，是洗脑。

### 8.1 Schema 追加（`memory_os/schema.py`）

```python
@dataclass
class SelfNote:            # L4
    id: str = ""           # f"self_{uuid4().hex[:10]}"
    content: str = ""      # 一句话，≤120 字
    note_type: str = "成长"  # 成长 | 关系 | 表达 | 情绪
    created_at: float = 0.0
    updated_at: float = 0.0
    mention_count: int = 1

@dataclass
class MemorySnapshot:      # L3 索引项
    id: str = ""           # f"snap_{uuid4().hex[:10]}"
    created_at: float = 0.0
    label: str = ""        # 中文可读时间，如 11-04 03:12
    episode_id: str = ""
    episode_title: str = ""
    facts_added: int = 0
    facts_updated: int = 0
    self_note: str = ""
    path: str = ""         # 相对项目根，如 data/archive/温柔老父亲/snap_xxx.json
```

`RoleMemory` 追加两个字段：`self_notes: list[SelfNote]`、`snapshots: list[MemorySnapshot]`；
上限 `SELF_NOTE_MAX=40`、`SNAPSHOT_MAX=30`（超出丢最旧）；`version` 升到 **4**，`load` 必须兼容 3（缺两字段用空列表）。

### 8.2 `consolidate` 追加（L3 + L4 的写入点）

1. LLM JSON 追加可选字段 `"self_note": {"content": "...", "note_type": "成长"} | null`；`prompts.heuristic_extract()` 兜底时返回 `None`。
2. `self_note.content` 与已有 selfNote 相似度 `>= SELF_NOTE_MERGE_SIM(0.8)` → `mention_count += 1`、`updated_at = now`（不新增）；否则新增。截断到 120 字、上限 40 条。
3. 每次**真正发生固化**（不论 LLM 成功或正则兜底）都写一份快照文件到 `data/archive/<角色>/<snapshot_id>.json`，内容 `{id, created_at, episode, facts_added[], facts_updated[], self_note, notes}`；写盘失败只 `notes.append("快照写入失败：…")`，**绝不影响主流程**。索引写入 `RoleMemory.snapshots`。

### 8.3 `engine` 新增 API（`memory_os/__init__.py` 一并导出需要的类型）

```python
search_archive(role, query, *, limit=5, now=None) -> list[dict]
# L0：对 data["chats"][role] 全量消息做 TF-IDF 检索（textsim.similarity，按分句最大余弦）
# 返回 [{"id","ts","ts_label","sender":"user|assistant","text","score"}]，按 score 降序

archive_stats(role) -> dict
# {"messages": int, "user_messages": int, "first_label": str, "last_label": str}

self_notes(role, *, limit=3) -> list[dict]
# [{"id","content","note_type","created_label","mention_count"}]

correct_fact(role, fact_id, new_content, *, category=None, now=None) -> dict | None
# L2 纠正：新建 Fact(confidence=CORRECTION_CONFIDENCE(0.9), evidence_count=旧+1,
#   origin="user_correction", first_seen=now, last_confirmed=now)；
#   旧 fact.superseded_by = 新 id（保留留档）；返回新 fact 的 snapshot 形 dict；找不到 fact_id 返回 None

list_snapshots(role, *, limit=5) -> list[dict]
# [{"id","label","episode_title","facts_added","facts_updated","self_note"}]，按时间倒序

build_memory_block(bundle, *, include_self=False) -> str
# include_self=True 时在末尾追加一节：
# 【它的自我叙述】\n- …（最多 3 条，绝不出现 "None"）
```

`retrieve(role, query, emotion="平静", *, tiers=None, include_self=False, now=None)` 同理把 `include_self` 传到 `MemoryBundle`（字段名 `include_self: bool = False`，`self_notes: list[dict]`）。

### 8.4 `snapshot_for_ui` 追加键（只增不改）

```python
"self":      {"items": [{"id","content","note_type","created_label","mention_count"}...], "count": int},
"archive":   {"messages": int, "first_label": str, "last_label": str},
"snapshots": {"items": [{"id","label","episode_title","facts_added","facts_updated"}...], "count": int},
```

### 8.5 UI 注入策略（`ui/state.py` 负责）

- `st.session_state["self_note_seeded"]` 默认 `False`；每轮 `chat_once` 读一次：
  `include_self = not seeded and 已有固化过（role.turn_counter >= CONSOLIDATE_EVERY_TURNS）`，
  命中则 `retrieve(..., include_self=True)` 并在返回 dict 里带 `self_note_used=True`，随后置 `True`。
- 切换角色 / 清空记忆时重置为 `False`。
- `build_memory_block(bundle, include_self=bundle.include_self)` 由 `ui/state.py` 调用。

---

## 9. 界面契约（v4，三视图）

### 9.1 视图与归属

| 视图 | 常量 | 内容 |
|---|---|---|
| 💬 对话 | `VIEW_CHAT` | 只放对话：气泡流（含 emotion / initiative / loop_hint / memory_update 四行 caption）、失败重试、「让 TA 主动开口」（门闩：`openers and snapshot["loops"]`）、`st.chat_input` |
| 🧠 记忆舱 | `VIEW_MEMORY` | `st.tabs(["五层总览","L0 原文库","L1 会话层","L2 长期事实","L3 固化快照","L4 自我认知","关系档案"])` |
| 🧪 实验室 | `VIEW_LAB` | `st.tabs(["本轮 Trace","消融开关","记忆体检"])` |

- 视图状态存 `st.session_state["view"]`，由 `st.radio`（`horizontal=True`, `label_visibility="collapsed"`）驱动；`ui/state.init_session()` 里 `setdefault("view", VIEW_CHAT)`。
- 亲密/信任、三层统计、Trace、关系档案**不再**占据对话页右栏；对话页做满宽。
- 顶部标题行右侧固定一行五层计数：`L0 原文 N 条 · L1 会话 N 条 · L2 事实 N 条 · L3 快照 N 份 · L4 自述 N 条`，全部用 `(snapshot.get(key) or {}).get(...)` 兜底，缺键显示 0。

### 9.2 记忆主权（可查 / 可删 / 可改）

| 能力 | 实现位置 | 契约 |
|---|---|---|
| 查 | `ui/panels.render_archive` → `ui/state.search_archive(role, query)` | 返回 `[{id, ts, ts_label, sender, text, score}]`；优先用 `engine.search_archive`，缺失时回落本地 `memory_os.similarity` + 分句最大余弦 |
| 删 | `ui/panels.render_mid/render_long` → `ui/state.forget_memory(role, item_id, *, tier)` | `tier ∈ {mid, long, shared}`；真删单条并 `store.save()`；返回 `bool` |
| 改 | `ui/panels.correct_dialog` → `ui/state.correct_fact(role, fact_id, new_content, *, category=None)` | 新 Fact `id=fact_<uuid10>`、`confidence=cfg.CORRECTION_CONFIDENCE(0.9)`、`evidence_count=旧+1`、`origin="user_correction"`；旧 fact `superseded_by=新id` |

- `correct_fact` 在 `ui/state.py` 内直接读改写 `store`，**不依赖** `engine.correct_fact`（两层实现互不阻塞，行为一致）。
- 删除/纠正后必须 `state.flash(...)` + `st.rerun()`；按钮 key 分别为 `forget_{tier}_{id}` / `correct_{tier}_{id}`（避免重复 key）。
- 清空记忆（`reset_role_memory` / `reset_everything`）必须同时把 `st.session_state["self_note_seeded"]` 置回 `False`。

## 10. 两段式单轮 + 人格教练（v4.1 新增）

### 10.1 一轮拆两段（`ui/state.py`）

| 函数 | 契约 |
|---|---|
| `begin_turn(role, user_text) -> str` | 只写用户那一条（`append_chat(role,"user",text,emotion=demo.detect_emotion(text))`），返回情绪先验；调用方随后立刻 `st.rerun()` 让气泡上屏 |
| `finish_turn(role, user_text, *, est_emotion=None) -> dict` | 取历史 → `retrieve` → `build_memory_block` → `build_system_prompt` → `structured_chat` → `record_turn` → 落盘；若 `_user_tail_pending()` 为真，**只追加 assistant 一条**，否则退回 `append_exchange` 写两条 |
| `chat_once(role, user_text)` | 薄的兼容封装 = `begin_turn` + `finish_turn`，供脚本/测试使用 |

- `app.py` 用 `st.session_state["pending_turn"] = {role, text, est_emotion}` 在两次 rerun 之间传递上下文；弹掉后若 `role` 已切换则丢弃（不串角色）。
- 「重试这一句」调 `finish_turn`，**不重写**用户那句（它早已落盘）。
- 切角色 / 清空记忆时必须同时清 `pending_turn`。

### 10.2 人格教练（`persona_forge/interview.py` + `ui/forge.render_coach_panel`）

- 状态：`{turns, answers, index, question, brief, name_hint, knobs, done}`，存 `st.session_state["coach"]`；`STAGES` 固定五问（relation / speech / emotion / boundary / spark）。
- 提问：`ask_question(state, *, llm=None)` → `call_json(purpose="persona_interview", demo_context={"stage","answers","index"})`，只接受 4–80 字的 `question`，异常回落 `stage["question"]`。
- 收束：`compose(state, *, llm=None)` → `call_json(purpose="persona_brief")`，只接受 `brief>=20` 字，`knobs` 逐键 clamp(0,1)，任一异常整段回落 `fallback_brief(answers)`。
- 编译：`compile_persona(brief, knobs=knobs)`，与「⚒ 锻造新角色」完全同一条流水线；结果存 `st.session_state["coach_result"]`，保存走 `state.save_forged_role(...)` 并把 `pending_view` 设为「💬 对话」。
- `core/demo.py` 必须支持 `persona_interview` / `persona_brief` 两个 purpose（离线答辩演示用），措辞要承接上一句回答里的原词。

---

## 11. 头像与运行模式（v4.2 新增）

### 11.1 头像：emoji 或上传的图片

| 模块 | 契约 |
|---|---|
| `core/config.py` | `AVATAR_DIR = DATA_DIR / "avatars"`、`AVATAR_PX = 96`、`AVATAR_MAX_BYTES = 4 * 1024 * 1024` |
| `ui/avatars.py` | `is_image(value)`、`store(data, *, kind, name="") -> token`（校验文件头，只收 PNG/JPG/GIF/WEBP，超限抛 `ValueError`）、`path_of(value)`（只认文件名，挡路径穿越）、`data_uri(value)`（压缩后 base64）、`display(value)`（标题/caption 用的占位 `🖼`）、`inline(value, *, size=26)`、`html_avatar(value, *, css_class="emo-avatar")`、`discard(value)` |
| token 形态 | `img:<文件名>`，例 `img:user_ab12cd34ef.png`；**任何存储（settings.json / 角色文件）里只记 token，绝不记图片字节** |
| 渲染 | 气泡与页头都用 `<img>` + `data:` URI（`ui/chat.py`、`app.py` 页头），不引入静态文件服务 |
| 清洗 | `ui/state.py::_clean_avatar()`：图片 token 原样保留，其余最长 4 字符；`user_avatar()`/`set_user_avatar()`/`set_role_avatar()`/`set_forged_avatar` 一律走它 |
| 生命周期 | 换头像 / 删角色时 `avatars.discard(旧 token)`；`.gitignore` 排除 `data/avatars/` |

- 上传入口：侧边栏「我的样子（头像）」与「人设卡（TA 是谁）」各一个 `st.file_uploader`，widget key 带 `avatar_upload_nonce`（`state.avatar_upload_key()` / `state.role_avatar_upload_key(role)` / `state.bump_avatar_upload()`），存完换 key，避免同一张图反复触发保存。
- 预设 pills 的取值必须始终是 options 里的一项：当前是图片头像时，options 末尾追加 `avatars.PLACEHOLDER`（`🖼`），default 设为它；否则 AppTest 重跑时 `pills.value` 为 `None` 会崩（`element_tree.py` 的 `indices`）。

### 11.2 运行模式优先级

| 情形 | `is_demo_mode()` | `mode_label()` |
|---|---|---|
| 本会话填了合法 Key | `False`（**最高优先**） | `在线模型 <model> · 本会话 Key sk-…尾4` |
| 无会话 Key，但服务端有 `DEEPSEEK_API_KEY` | `False` | `在线模型 <model> · 服务端 Key sk-…尾4` |
| 无 Key 且 `EMO_DEMO_MODE=1` | `True` | `离线演示模式（EMO_DEMO_MODE=1 强制；在本页填一个 Key 就会切回在线）` |
| 无 Key 且无强制 | `True` | `离线演示模式（还没填 Key，本地假模型全链路可跑）` |

- `forced_demo()` 只表示"服务端开了强制演示"；它**不能**压过访客自己的 Key，否则侧边栏会同时出现「已使用你的 Key」与「离线演示模式」。
- 占位符 Key（含 `xxxx` / 不以 `sk-` 开头 / 太短）按"没填"处理（`key_looks_placeholder()`），并提示具体原因。
- `ui/state.py::api_key_hint()` / `demo_hint()` 是这两句文案的唯一来源，别在 `app.py` 里另写一套。

## 12. 主动开口的六种句型 + 打字态（v4.3 新增）

### 12.1 句型表（`memory_os/engine.py`）

| style | 中文 | 解锁阶段（`_STYLE_MIN_STAGE`） | 是否问句 |
|---|---|---|---|
| `ask` | 问一句 | 陌生(0) | ✅ 必须带 `？` |
| `care` | 关心一句 | 陌生(0) | ❌ |
| `presence` | 就在旁边 | 陌生(0) | ❌ |
| `recall` | 提起旧事 | 熟悉(1) | ❌ |
| `tease` | 打趣 | 亲近(2) | ❌ |
| `self` | 说说自己 | 亲近(2) 且有 L4 自我认知 | ❌ |

- `proactive_suggestions(role, *, limit=3, now=None, polish=True, commit=True)` 返回
  `[{opener, style, label, hook, hook_kind, reason}]`；同一批句型不重复。
- `commit=False` 时**不写** `data.meta.proactive_last_style`（界面只是"先给你看看"，
  不能每刷一次就换一句）。
- `polish=False` 时不调模型（候选就是模板）；真正落到对话里才 `polish=True` 润色一次。
- `_fits_style()` 是硬约束：`ask` 不带问号、或其它句型偷写成问句，一律回落到模板。
- `proactive_openers()` 是只要文本的旧接口（`polish=True, commit=True`），脚本/测试沿用。

### 12.2 打字态与已读（`ui/chat.py` + `app.py`）

- `typing_html(role_name, role_avatar)` / `render_typing(...)`：三颗闪烁点 + 小字「正在输入…」。
- `row_html(..., read=True)` 才在用户气泡下渲染「已读」；`render_thread` 按"后面还有 assistant 内容"判定。
- 顺序（不可颠倒）：用户气泡（`begin_turn`）→ 打字态 → `finish_turn` → `rerun`。
- `cfg.TYPING_MIN_MS = 900`：即使模型 900ms 内返回，打字态也至少停留这么久；打字期间
  `st.chat_input(disabled=True)`，避免连发丢消息。
- 单测里把 `cfg.TYPING_MIN_MS` patch 成 0（`tests/test_app.py` 的 fixture 已这么做）。
