"""记忆引擎的离线单测：无网络、无 sleep、时间全部注入、LLM 全部用 FakeLLM 替换。

覆盖 SPEC §2/§3.1/§3.2/§3.3/§6 的关键行为：持久化、相似度、固化、冲突消解、
情绪门控、预算裁剪、遗忘与召回强化、降级兜底、关系档案、UI 快照键名。
"""

from __future__ import annotations

import json
import math

import pytest

from core.config import (
    MEMORY_BLOCK_BUDGET,
    W_EMO,
    W_IMP,
    W_REC,
    W_SIM,
    MemoryConfig,
    stage_of,
)
from memory_os import MemoryEngine, MemoryStore, decay, similarity
from memory_os.schema import (
    Fact,
    WorkingTurn,
    default_data,
    get_role_memory,
    get_shared_facts,
    set_role_memory,
)

ROLE = "林医生"
OTHER = "阿远"
T0 = 1_700_000_000_000.0
DAY = 86_400_000.0


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class FakeLLM:
    """可脚本化的假 LLM：按 purpose 返回固定 payload，或抛异常模拟故障。"""

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, system_prompt, user_prompt, *, temperature=0.6, max_tokens=1600,
                 purpose="json", demo_context=None):
        self.calls.append(
            {
                "purpose": purpose,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "demo_context": demo_context,
            }
        )
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.payload))


def make_payload(**overrides) -> dict:
    """构造一份合法的固化 JSON（结构与 prompts.CONSOLIDATE_SYSTEM 对齐）。"""
    payload = {
        "episode": {
            "title": "找实习的焦虑",
            "summary": "用户在为找实习焦虑，提到妈妈总催他，聊完松了一点。",
            "topics": ["实习", "家庭"],
            "keywords": ["实习", "妈妈"],
            "valence": -0.6,
            "arousal": 0.8,
            "importance": 0.85,
        },
        "facts": [
            {"content": "用户是计算机专业大三学生", "category": "身份", "confidence": 0.9},
            {"content": "用户讨厌被催找工作", "category": "厌恶", "confidence": 0.8},
        ],
        "loops_open": [{"what": "下周三有实习面试", "kind": "承诺", "due_hint": "下周三"}],
        "loops_close": [],
        "nickname": None,
        "relationship": {"trust_delta": 1.0, "inside_joke": None, "shared_theme": None},
    }
    payload.update(overrides)
    return payload


def make_engine(tmp_path, payload: dict | None = None, *, error: Exception | None = None,
                **config_kwargs) -> tuple[MemoryEngine, FakeLLM]:
    """建一个临时库 + FakeLLM 的引擎，返回 (engine, fake)。"""
    llm = FakeLLM(payload if payload is not None else make_payload(), error=error)
    config = MemoryConfig(**config_kwargs) if config_kwargs else MemoryConfig()
    return MemoryEngine(MemoryStore(tmp_path / "memory.json"), llm=llm, config=config), llm


def talk(engine: MemoryEngine, rounds: int, *, now: float = T0, emotion: str = "难过",
         start: int = 0) -> None:
    """灌 ``rounds`` 轮对话（每轮一问一答）。"""
    for index in range(start, start + rounds):
        engine.record_turn(
            ROLE,
            f"第{index}轮：我在找实习，我妈又催我了，心里堵得慌。",
            "我在，先陪你把这口气喘匀，我们再一条条看。",
            emotion,
            now=now + index * 60_000,
        )


def seed_facts(engine: MemoryEngine, role: str, facts: list[Fact]) -> None:
    """直接往库里塞事实（跳过 LLM，用于构造检索 / 遗忘场景）。"""
    memory = get_role_memory(engine.data, role)
    memory.facts.extend(facts)
    set_role_memory(engine.data, role, memory)
    engine.save()


# ---------------------------------------------------------------------------
# 1. 持久化：往返、原子写、旧格式迁移、破损容错
# ---------------------------------------------------------------------------
def test_store_roundtrip_atomic_and_migration(tmp_path):
    """store 能往返、原子落盘、迁移 v2 / 平铺旧格式、破损文件不崩。"""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)

    data = default_data()
    memory = get_role_memory(data, ROLE)
    memory.turns.append(WorkingTurn(role="user", content="你好", ts=T0, emotion="开心"))
    memory.facts.append(Fact(content="用户喜欢猫", category="喜好", confidence=0.8,
                             first_seen=T0, last_confirmed=T0))
    set_role_memory(data, ROLE, memory)
    store.save(data)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 3
    assert list(path.parent.glob("*.tmp")) == []  # 原子写不留临时文件

    loaded = store.load()
    assert loaded["version"] == 3
    assert get_role_memory(loaded, ROLE).turns[0].content == "你好"
    assert get_role_memory(loaded, ROLE).facts[0].content == "用户喜欢猫"

    # v2 结构：chats + memories(list[str])
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "chats": {ROLE: [{"role": "user", "content": "旧消息", "ts": T0 / 1000}]},
                "memories": ["用户养了一只橘猫"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    migrated = store.load()
    assert migrated["version"] == 3
    assert migrated["chats"][ROLE][0]["content"] == "旧消息"
    migrated_role = get_role_memory(migrated, ROLE)
    assert migrated_role.turns, "v2 的聊天记录应预热成短期记忆"
    assert migrated_role.turns[0].ts >= 1e11, "秒级时间戳应迁移成毫秒"
    assert any(fact.content == "用户养了一只橘猫" for fact in migrated_role.facts)

    # 更旧的平铺格式：{"角色名": [msgs]}
    path.write_text(json.dumps({ROLE: [{"role": "user", "content": "远古消息", "ts": T0}]},
                               ensure_ascii=False), encoding="utf-8")
    flat = store.load()
    assert flat["version"] == 3
    assert flat["chats"][ROLE][0]["content"] == "远古消息"

    # 破损 JSON：不抛异常、留 .bak、重建空库
    path.write_text('{"version": 3, "roles": {', encoding="utf-8")
    recovered = store.load()
    assert recovered == default_data()
    assert (tmp_path / "memory.bak").exists()


# ---------------------------------------------------------------------------
# 2. 文本相似度
# ---------------------------------------------------------------------------
def test_textsim_similarity_behaviour():
    """字符 2-gram 余弦：相似句得分高于不相似句，空串 / 纯标点不崩。"""
    assert similarity("用户喜欢在周末去图书馆看书", "用户喜欢在周末去图书馆看书") == pytest.approx(1.0)
    similar = similarity("用户在找实习", "用户正在找实习")
    unrelated = similarity("用户在找实习", "今天天气真不错")
    assert similar > unrelated
    assert similarity("用户喜欢猫", "用户讨厌猫") < similarity("用户喜欢猫", "用户喜欢橘猫")
    # 边界：空串 / 单字符 / 纯标点 / 全空白
    for left, right in [("", ""), ("猫", "猫"), ("！？。", "，；："), ("   ", "用户")]:
        score = similarity(left, right)
        assert isinstance(score, float) and 0.0 <= score <= 1.0
    assert similarity("", "用户喜欢猫") == 0.0
    assert similarity("用户喜欢猫", "") == 0.0


# ---------------------------------------------------------------------------
# 3. 短期累积与自动固化条件
# ---------------------------------------------------------------------------
def test_record_turn_accumulates_and_auto_consolidate(tmp_path):
    """record_turn 累积 turn；攒够 6 条且轮数到 4 的整数倍才自动固化。"""
    engine, fake = make_engine(tmp_path)
    talk(engine, 3)
    memory = get_role_memory(engine.data, ROLE)
    assert len(memory.turns) == 6
    assert memory.turn_counter == 3
    assert engine.pending_turns(ROLE) == 6
    assert engine.last_consolidation is None
    assert engine.should_consolidate(ROLE) is False  # 3 % 4 != 0

    talk(engine, 1, start=3)
    result = engine.last_consolidation
    assert result is not None and result.done is True
    assert engine.pending_turns(ROLE) == 0
    assert fake.calls[-1]["purpose"] == "consolidate"
    assert fake.calls[-1]["demo_context"]["turns"], "§4.1：demo_context 要带 turns"
    assert get_role_memory(engine.data, ROLE).relationship.turn_count == 4

    # 关掉自动固化后，record_turn 不再触发，但 should_consolidate 仍能提示
    manual, _ = make_engine(tmp_path / "manual", auto_consolidate=False)
    talk(manual, 4)
    assert manual.last_consolidation is None
    assert manual.should_consolidate(ROLE) is True
    assert manual.consolidate(ROLE, now=T0).done is True
    assert manual.last_consolidation is not None

    # 轮数不足且非 force → done=False
    short, _ = make_engine(tmp_path / "short")
    talk(short, 1)
    assert short.consolidate(ROLE, now=T0).done is False


# ---------------------------------------------------------------------------
# 4. 固化正常路径
# ---------------------------------------------------------------------------
def test_consolidate_happy_path(tmp_path):
    """force=False 也能在 turn 够时固化：episode 入 mid、fact 入 long、loop 打开。"""
    engine, _ = make_engine(tmp_path)
    talk(engine, 3)
    result = engine.consolidate(ROLE, now=T0)
    assert result.done is True
    assert result.used_llm is True
    assert result.episode_id
    assert result.new_facts and result.loops_opened == ["下周三有实习面试"]
    assert result.relationship["after"]["stage"] == "陌生"

    memory = get_role_memory(engine.data, ROLE)
    assert any(fact.content == "用户是计算机专业大三学生" for fact in memory.facts)
    assert memory.open_loops[0].status == "open"
    assert memory.last_consolidated_turn == len(memory.turns)

    bundle = engine.retrieve(ROLE, "下周三实习面试准备得怎么样", now=T0 + 60_000)
    for section in ("【中期·相关事件】", "【长期·稳定事实】", "【未闭环的事】", "【短期·最近对话】"):
        assert section in bundle.block
    assert bundle.episodes and any(item.tier == "mid" for item in bundle.trace.items)
    # §3.1 分数公式逐项可复算
    for item in bundle.trace.items:
        components = item.components
        expected = (W_SIM * components["sim"] + W_IMP * components["importance"]
                    + W_REC * components["recency"] + W_EMO * components["emotion"])
        if not item.pinned and item.tier == "mid":
            assert item.score == pytest.approx(min(1.0, expected), abs=1e-3)


# ---------------------------------------------------------------------------
# 5. Fact 合并 / 冲突消解
# ---------------------------------------------------------------------------
def test_fact_merge_and_conflict_resolution(tmp_path):
    """高相似同分类 → 合并计数；矛盾表述 → 旧事实被取代且不再注入。"""
    engine, fake = make_engine(tmp_path)
    fact_a = {"content": "用户喜欢在周末去图书馆看书", "category": "喜好", "confidence": 0.7}
    fake.payload = make_payload(facts=[fact_a], loops_open=[])
    talk(engine, 3)
    first = engine.consolidate(ROLE, now=T0)
    assert len(first.new_facts) == 1

    talk(engine, 3, start=3)
    second = engine.consolidate(ROLE, now=T0 + 3 * 60_000)
    merged = [fact for fact in get_role_memory(engine.data, ROLE).facts
              if fact.content.startswith("用户喜欢在周末去图书馆")]
    assert len(merged) == 1, "高相似同分类应合并为同一条"
    assert merged[0].evidence_count == 2
    assert merged[0].confidence == pytest.approx(0.8)  # min(1, 0.7+0.1)
    assert second.updated_facts, "合并要记进 updated_facts"

    # 冲突：否定词差异
    fake.payload = make_payload(
        facts=[{"content": "用户不喜欢在周末去图书馆看书", "category": "喜好", "confidence": 0.75}],
        loops_open=[],
    )
    talk(engine, 3, start=6)
    third = engine.consolidate(ROLE, now=T0 + 6 * 60_000)
    assert third.superseded, "矛盾表述应触发冲突消解"
    facts = get_role_memory(engine.data, ROLE).facts
    old = [fact for fact in facts if fact.content.startswith("用户喜欢在周末去图书馆")][0]
    new = [fact for fact in facts if fact.content.startswith("用户不喜欢在周末去图书馆")][0]
    assert old.superseded_by == new.id
    assert new.confidence == pytest.approx(0.75)

    bundle = engine.retrieve(ROLE, "周末去图书馆看书", now=T0 + 7 * 60_000)
    assert "用户不喜欢在周末去图书馆看书" in bundle.block
    assert "用户喜欢在周末去图书馆看书" not in bundle.block, "被取代的旧事实不应再注入"

    # 消融实验：关掉冲突消解 → 不写 superseded_by
    off, off_llm = make_engine(tmp_path / "off", conflict_resolution=False)
    off_llm.payload = make_payload(facts=[fact_a], loops_open=[])
    talk(off, 3)
    off.consolidate(ROLE, now=T0)
    off_llm.payload = make_payload(
        facts=[{"content": "用户不喜欢在周末去图书馆看书", "category": "喜好", "confidence": 0.75}],
        loops_open=[],
    )
    talk(off, 3, start=3)
    result_off = off.consolidate(ROLE, now=T0 + 3 * 60_000)
    assert result_off.superseded == []
    assert all(fact.superseded_by is None for fact in get_role_memory(off.data, ROLE).facts)
    assert any("冲突" in note for note in result_off.notes)


# ---------------------------------------------------------------------------
# 6. 情绪门控
# ---------------------------------------------------------------------------
def test_mood_gate_skips_negative_episode(tmp_path):
    """难过 + 高唤醒 + 低相似 → 跳过消极旧事并写 trace.skipped。"""
    engine, _ = make_engine(tmp_path, make_payload(
        episode={**make_payload()["episode"], "valence": -0.9, "arousal": 0.95}
    ))
    talk(engine, 3)
    engine.consolidate(ROLE, now=T0)

    bundle = engine.retrieve(ROLE, "中午吃点什么好呢", "难过", now=T0 + 120_000)
    assert bundle.trace.skipped, "情绪门控应记下被跳过的记忆"
    assert all(item.tier == "mid" for item in bundle.trace.skipped)
    assert "【中期·相关事件】" not in bundle.block, "被门控的旧事不应注入记忆块"
    assert bundle.episodes == []
    assert any("门控" in note for note in bundle.trace.notes)

    # 同一批记忆，情绪平静时应该正常召回
    calm = engine.retrieve(ROLE, "找实习", "平静", now=T0 + 180_000)
    assert any(item.tier == "mid" for item in calm.trace.items)


# ---------------------------------------------------------------------------
# 7. 预算裁剪
# ---------------------------------------------------------------------------
def test_budget_trims_memory_block(tmp_path):
    """塞 30 条事实 → 记忆块不超过预算，且 trace.notes 记录裁剪。"""
    engine, _ = make_engine(tmp_path, block_budget=400, short_term_inject_turns=2)
    talk(engine, 5)
    facts = [
        Fact(
            content=f"用户第{index}次提到自己在准备「分布式系统」课程的大作业，并记下了一堆细节",
            category="近况",
            confidence=0.6,
            first_seen=T0,
            last_confirmed=T0,
        )
        for index in range(30)
    ]
    seed_facts(engine, ROLE, facts)
    bundle = engine.retrieve(ROLE, "分布式系统课程大作业", now=T0 + 60_000)
    assert len(bundle.block) <= 400
    assert len(bundle.block) <= MEMORY_BLOCK_BUDGET
    assert len(bundle.facts) < 30
    assert any("预算裁剪" in note for note in bundle.trace.notes)
    assert bundle.trace.budget == 400


# ---------------------------------------------------------------------------
# 8. 遗忘曲线与召回强化
# ---------------------------------------------------------------------------
def test_forgetting_archive_and_recall_reinforcement(tmp_path):
    """60 天后弱事实被归档且不再注入；召回时 recall_count / base_strength 按公式变化。"""
    engine, _ = make_engine(tmp_path)
    weak = Fact(
        content="用户喜欢喝手冲咖啡", category="近况", confidence=0.3,
        base_strength=0.5, first_seen=T0, last_confirmed=T0,
    )
    identity = Fact(
        content="用户是计算机专业大三学生", category="身份", confidence=0.95,
        first_seen=T0, last_confirmed=T0,
    )
    seed_facts(engine, ROLE, [weak, identity])
    seed_facts(engine, OTHER, [Fact(
        content="用户喜欢喝手冲咖啡", category="近况", confidence=0.3,
        first_seen=T0, last_confirmed=T0,
    )])

    # 召回即强化
    bundle = engine.retrieve(ROLE, "手冲咖啡", now=T0)
    assert "用户喜欢喝手冲咖啡" in bundle.block
    recalled = [fact for fact in get_role_memory(engine.data, ROLE).facts
                if fact.content == "用户喜欢喝手冲咖啡"][0]
    assert recalled.recall_count == 1
    assert recalled.base_strength == pytest.approx(0.6)  # min(1, 0.5+0.1)
    assert recalled.last_confirmed == T0
    expected = min(1.0, 0.6 * math.exp(-0.0) * (1 + 0.12 * 1))
    assert decay.strength(recalled, T0) == pytest.approx(expected)

    # 另一个角色完全不召回：60 天后必然跌破阈值被归档
    older = get_role_memory(engine.data, OTHER).facts[0]
    assert decay.strength(older, T0 + 60 * DAY) < 0.15
    stats = engine.decay_pass(OTHER, now=T0 + 60 * DAY)
    assert stats["archived"] == 1
    assert stats["weakest"] < 0.15
    archived_view = engine.snapshot_for_ui(OTHER, now=T0 + 60 * DAY)
    assert archived_view["long"]["items"][0]["archived"] is True
    assert get_role_memory(engine.data, OTHER).facts[0].archived is True

    bundle_later = engine.retrieve(OTHER, "手冲咖啡", now=T0 + 60 * DAY)
    assert "用户喜欢喝手冲咖啡" not in bundle_later.block, "归档记忆不应注入"


# ---------------------------------------------------------------------------
# 9. LLM 故障降级
# ---------------------------------------------------------------------------
def test_llm_failure_uses_heuristic_fallback(tmp_path):
    """LLM 抛异常 → 规则兜底、used_llm=False、仍产出 episode。"""
    engine, _ = make_engine(tmp_path, error=RuntimeError("模拟网络故障"))
    talk(engine, 3)
    result = engine.consolidate(ROLE, now=T0)
    assert result.done is True
    assert result.used_llm is False
    assert result.episode_id
    memory = get_role_memory(engine.data, ROLE)
    assert memory.episodes and memory.episodes[0].title
    assert any("兜底" in note for note in result.notes)

    # 字段类型全错也不整条失败：逐字段降级
    bad, _ = make_engine(tmp_path / "bad", {
        "episode": "这不是 dict",
        "facts": [{"content": 123}, {"content": "用户喜欢猫", "category": "乱七八糟",
                                      "confidence": "很高"}],
        "loops_open": "这不是列表",
        "loops_close": None,
        "nickname": 42,
        "relationship": "这不是 dict",
    })
    talk(bad, 3)
    degraded = bad.consolidate(ROLE, now=T0)
    assert degraded.done is True
    degraded_facts = get_role_memory(bad.data, ROLE).facts
    assert any(fact.content == "用户喜欢猫" and fact.category == "近况" for fact in degraded_facts)
    assert get_role_memory(bad.data, ROLE).episodes[0].title


# ---------------------------------------------------------------------------
# 10. 关系档案
# ---------------------------------------------------------------------------
def test_relationship_profile_evolves(tmp_path):
    """intimacy 单调不减、stage 随阈值前进、昵称变更留历史。"""
    payload = make_payload(nickname="小陈", relationship={"trust_delta": 1.0})
    engine, fake = make_engine(tmp_path, payload, auto_consolidate=False)

    # 第一轮只走基础递增：base=0.8*1 个用户轮 + 难过安抚 0.6 = 1.4
    talk(engine, 1, now=T0)
    engine.consolidate(ROLE, force=True, now=T0 + 1000)
    first = get_role_memory(engine.data, ROLE).relationship
    assert first.intimacy == pytest.approx(1.4)
    assert first.trust == pytest.approx(1.0)

    intimacies: list[float] = [first.intimacy]
    stages: list[str] = [first.stage]
    fake.payload["relationship"] = {"trust_delta": 1.0, "intimacy_delta": 3.0}
    for index in range(1, 20):
        if index == 12:
            fake.payload["nickname"] = "小陈同学"
        talk(engine, 1, now=T0 + index * 60_000)
        engine.consolidate(ROLE, force=True, now=T0 + index * 60_000 + 1000)
        relationship = get_role_memory(engine.data, ROLE).relationship
        intimacies.append(relationship.intimacy)
        stages.append(relationship.stage)

    assert all(later >= earlier for earlier, later in zip(intimacies, intimacies[1:]))
    assert all(intimacies) and intimacies[-1] > 45
    order = ["陌生", "熟悉", "亲近", "亲密"]
    assert [order.index(stage) for stage in stages] == sorted(order.index(stage) for stage in stages)
    final = get_role_memory(engine.data, ROLE).relationship
    assert final.stage == stage_of(final.intimacy)
    assert final.nickname == "小陈同学"
    assert any(item["value"] == "小陈" for item in final.nickname_history)
    assert final.turn_count == 20
    assert final.inside_jokes == [] or isinstance(final.inside_jokes, list)
    assert engine.snapshot_for_ui(ROLE, now=T0)["relationship"]["stage"] == final.stage


# ---------------------------------------------------------------------------
# 11. UI 快照键名（严格按 SPEC §6）
# ---------------------------------------------------------------------------
def test_snapshot_keys_match_spec(tmp_path):
    """snapshot_for_ui 的键集合必须与 SPEC §6 完全一致（键名错 UI 会白屏）。"""
    engine, _ = make_engine(tmp_path, make_payload(relationship={"trust_delta": 2.0,
                                                                "shared_theme": "找实习"}))
    talk(engine, 3)
    engine.consolidate(ROLE, now=T0)
    snapshot = engine.snapshot_for_ui(ROLE, now=T0 + 60_000)

    assert set(snapshot) == {"short", "mid", "long", "relationship", "loops", "stats",
                             "decay_curve"}
    assert set(snapshot["short"]) == {"turns", "summary", "count"}
    assert set(snapshot["short"]["turns"][0]) == {"role", "content", "ts_label", "emotion"}
    assert isinstance(snapshot["short"]["turns"][0]["ts_label"], str)
    assert set(snapshot["mid"]) == {"items"}
    assert set(snapshot["mid"]["items"][0]) == {
        "id", "title", "summary", "topics", "valence", "arousal", "importance",
        "strength", "recall_count", "created_label", "archived",
    }
    assert set(snapshot["long"]) == {"items", "shared"}
    assert set(snapshot["long"]["items"][0]) == {
        "id", "content", "category", "confidence", "evidence_count", "strength",
        "last_confirmed_label", "archived", "superseded_by",
    }
    assert isinstance(snapshot["long"]["items"][0]["last_confirmed_label"], str)
    assert set(snapshot["relationship"]) == {
        "intimacy", "trust", "stage", "nickname", "nickname_history", "inside_jokes",
        "shared_themes", "turn_count",
    }
    assert set(snapshot["loops"][0]) == {
        "id", "what", "kind", "due_hint", "status", "mention_count", "created_label",
    }
    assert set(snapshot["stats"]) == {
        "turns", "episodes", "facts", "shared_facts", "open_loops", "archived", "avg_strength",
    }
    curve = snapshot["decay_curve"]
    assert len(curve) == 14
    assert set(curve[0]) == {"label", "episode", "fact"}
    assert isinstance(curve[0]["label"], str)

    # 空 tiers 不能崩，只保留置顶兜底
    pinned_only = engine.retrieve(ROLE, "随便聊聊", tiers=set(), now=T0 + 60_000)
    assert isinstance(pinned_only.block, str)
    assert all(item.pinned for item in pinned_only.facts)
    assert pinned_only.working_turns == []
    # 记忆块末尾不留多余空行
    assert engine.build_memory_block(pinned_only) == engine.build_memory_block(pinned_only).strip()


def test_shared_facts_and_stats(tmp_path):
    """跨角色共享事实与统计口径（顶层 shared_facts + §6 stats）。"""
    engine, _ = make_engine(tmp_path)
    talk(engine, 3)
    engine.consolidate(ROLE, now=T0)
    engine.consolidate(ROLE, force=True, now=T0 + 60_000)  # 同一身份事实再次确认
    shared = get_shared_facts(engine.data)
    assert isinstance(shared, list)
    stats = engine.stats(ROLE)
    assert set(stats) == {"turns", "episodes", "facts", "shared_facts", "open_loops",
                          "archived", "avg_strength"}
    assert stats["turns"] == len(get_role_memory(engine.data, ROLE).turns)
    assert stats["open_loops"] == 1
    assert engine.close_loop(ROLE, get_role_memory(engine.data, ROLE).open_loops[0].id,
                             now=T0 + 90_000) is True
    assert engine.stats(ROLE)["open_loops"] == 0
    # 主动关怀：没有未闭环事件也要给一句兜底开场
    openers = engine.proactive_openers(ROLE, limit=2, now=T0 + 90_000)
    assert openers and all(isinstance(text, str) and text for text in openers)
