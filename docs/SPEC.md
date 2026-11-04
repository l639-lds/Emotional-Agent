# EmoMem 改造规格书（内部契约，所有协作者共同遵循）

> 本文件是**接口契约**，不是用户文档。任何人不得单方面改动签名；确需改动先在任务板上留言并等 Lead 确认。

## 0. 项目定位

原有项目：Streamlit + DeepSeek 的情感陪伴 Agent（单文件 `app.py`，876 行，JSON 存储，v2 schema）。
本次改造目标：把「单一 `memories: list[str]`」升级为**情感感知的三层记忆系统 EmoMem**，并把角色生成升级为**人设编译器 Persona Forge**。

对外卖点（海报/答辩用同一套表述，措辞不要各写一套）：

| # | 创新点 | 一句话 |
|---|---|---|
| 1 | 三层记忆 + 记忆固化 | 工作记忆（逐轮）→ 情景记忆（LLM 压缩成 Episode）→ 语义画像（长期 Fact），每 4 轮自动"睡眠整理" |
| 2 | 情绪加权检索 + 防翻旧账 | 每条记忆带效价 valence/唤醒度 arousal，检索分 = 相似度×重要性×时间衰减×情绪显著性；用户情绪负面时抑制未被提及的高唤醒负性记忆 |
| 3 | 记忆冲突消解 | 新旧事实矛盾时旧条目 `superseded_by` 标记作废（保留审计），置信度随证据数演化 |
| 4 | 可解释记忆 Trace | 每轮记录注入了哪些记忆、各项分值构成与理由，界面可展开查看，附「本轮未采用的记忆及原因」 |
| 5 | 关系档案驱动人格漂移 | intimacy/trust/stage/称呼演化/共同梗，阶段反向调节语气（陌生克制 → 亲密可调侃撒娇），人设不再静态 |
| 6 | 未闭环事件 + 主动关怀 | 从对话抽取"待跟进事项"（面试/体检/约定），下次主动追问；另有消融开关（现场对比关掉某层记忆的回复差异） |
| 7 | 人设编译器 Persona Forge | 一句描述 → 结构化人设卡（语音指纹/情绪反应表/关系弧/边界）→ 自评打分 → 定向重写 → 3 问人设体检 |

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
persona_forge/legacy.py     persona    v1 旧角色（persona/opening 字符串）兼容包装
tests/test_persona_forge.py persona    pytest，全部离线、确定性
docs/poster/*               poster     A2 海报（html/pdf/png）
docs/答辩稿.md / .docx        poster     一页答辩稿
```

## 2. 数据 Schema（`data/memory.json` v3）

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
