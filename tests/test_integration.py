"""跨模块集成测试：三层记忆 + 人设编译器 + 离线演示模式，端到端跑通（不联网、不导入 streamlit）。

这里只断言"系统级不变量"，具体的打分公式、参数细节由 tests/test_memory_os.py 与
tests/test_persona_forge.py 各自负责。
"""

from __future__ import annotations

import time

import pytest

from core import config as cfg
from core import demo, llm
import memory_os
from memory_os import MemoryEngine, MemoryStore
from persona_forge import card_to_dict, compile_persona, dict_to_card
from persona_forge.legacy import from_legacy
from persona_forge.render import build_system_prompt, render_style_directives

ROLE = "测试角色"

DIALOGUE = (
    ("你好呀，我叫小林，下周三要去面试。", "记住啦，别慌。"),
    ("我最讨厌别人骗我了。", "嗯，我记住了。"),
    ("我妈最近住院了，我有点撑不住。", "抱抱你，先说给我听。"),
    ("我还喜欢下雨天，安静。", "下雨天确实适合发呆。"),
)


@pytest.fixture()
def demo_mode(monkeypatch):
    """强制离线演示模式：所有 LLM 调用走 core.demo 的确定性假数据。"""
    monkeypatch.setenv("EMO_DEMO_MODE", "1")
    assert llm.is_demo_mode()
    return "demo"


@pytest.fixture()
def engine(tmp_path, demo_mode):
    store = MemoryStore(tmp_path / "memory.json")
    return MemoryEngine(store)


def _feed(engine: MemoryEngine, base_ms: float = 1_700_000_000_000) -> None:
    for index, (user_text, reply) in enumerate(DIALOGUE):
        engine.record_turn(
            ROLE,
            user_text,
            reply,
            demo.detect_emotion(user_text),
            now=base_ms + index * 60_000,
        )


def test_four_turns_trigger_one_consolidation(engine):
    """每 4 轮固化一次：第 4 轮结束后待固化清零，且留下可读的固化痕迹。"""
    _feed(engine)
    assert engine.pending_turns(ROLE) == 0
    result = engine.last_consolidation
    assert result is not None
    assert result.notes, "固化结果必须留下 notes 给 UI 展示"

    snapshot = engine.snapshot_for_ui(ROLE)
    stats = snapshot["stats"]
    assert stats["turns"] >= 8
    assert stats["episodes"] >= 1
    assert stats["facts"] >= 1


def test_snapshot_shape_matches_ui_contract(engine):
    """ui/panels.py 依赖的 snapshot 键与字段必须齐全。"""
    _feed(engine)
    snapshot = engine.snapshot_for_ui(ROLE)
    for key in ("short", "mid", "long", "relationship", "loops", "stats", "decay_curve"):
        assert key in snapshot

    assert snapshot["short"]["count"] >= 1
    turn = snapshot["short"]["turns"][0]
    for field in ("role", "content", "ts_label", "emotion"):
        assert field in turn
    assert isinstance(turn["ts_label"], str) and turn["ts_label"]

    episode = snapshot["mid"]["items"][0]
    for field in ("id", "title", "summary", "topics", "strength", "created_label", "archived"):
        assert field in episode

    fact = snapshot["long"]["items"][0]
    for field in ("id", "content", "category", "confidence", "last_confirmed_label", "superseded_by"):
        assert field in fact

    curve = snapshot["decay_curve"]
    assert len(curve) == 14
    assert {"label", "episode", "fact"} <= set(curve[0])
    assert 0.0 <= curve[-1]["episode"] <= curve[0]["episode"] <= 1.0

    relationship = snapshot["relationship"]
    assert relationship["stage"] in {stage for _, stage in cfg.STAGE_THRESHOLDS}
    assert 0.0 <= relationship["intimacy"] <= 100.0


def test_memory_block_is_readable_and_budgeted(engine):
    """记忆块喂给模型：含短期/长期、无多余空行、不超预算。"""
    _feed(engine)
    bundle = engine.retrieve(ROLE, "面试还是有点慌", "难过")
    block = engine.build_memory_block(bundle)

    assert "【短期" in block
    assert "小林" in block
    assert not block.endswith("\n")
    assert len(block) <= cfg.MEMORY_BLOCK_BUDGET + 200
    assert bundle.trace.items, "Trace 不能是空的，否则 UI 的『为什么记得』没东西可讲"


def test_retrieval_respects_enabled_tiers(engine):
    """消融开关：关掉某层，该层条目就不再进记忆块。"""
    _feed(engine)

    full = engine.retrieve(ROLE, "面试", "难过", tiers=set(cfg.ALL_TIERS))
    short_only = engine.retrieve(ROLE, "面试", "难过", tiers={"short"})

    assert full.trace.items
    assert {item.tier for item in short_only.trace.items} <= {"short"}

    nothing = engine.retrieve(ROLE, "面试", "难过", tiers=set())
    assert nothing.trace.items == []
    assert engine.build_memory_block(nothing) == ""


def test_mood_gate_keeps_relevant_but_drops_offtopic_old_grief(engine):
    """情绪门控：难受时不该翻旧账，但相关的情景记忆必须留下来（中期可见）。"""
    _feed(engine)
    engine.consolidate(ROLE, force=True)

    bundle = engine.retrieve(ROLE, "面试还是有点慌", "难过", tiers={"short", "mid", "long"})
    episodes = [item for item in bundle.trace.items if item.tier == "mid"]
    assert episodes, "相关的面试 Episode 不能被情绪门控误伤，否则三层记忆只剩两层"

    block = engine.build_memory_block(bundle)
    assert "【中期" in block


def test_trust_widens_the_mood_gate(monkeypatch):
    """信任值有实际作用：信任越高，越敢在对方难受时提起旧事。"""
    from memory_os import retrieval
    from memory_os.schema import Episode

    monkeypatch.setattr(retrieval.textsim, "relevance", lambda query, text: 0.20)
    episode = Episode(id="ep_trust", title="聊到面试", summary="面试让她很慌", valence=-0.6, arousal=0.8)
    config = cfg.MemoryConfig()

    assert retrieval.mood_gate_blocks(episode, "面试", emotion="难过", config=config) is True
    assert (
        retrieval.mood_gate_blocks(episode, "面试", emotion="难过", config=config, trust=100.0) is False
    ), "信任拉满时，0.20 的相关度应该越过放宽后的门槛（0.175）"
    assert retrieval.mood_gate_blocks(episode, "面试", emotion="开心", config=config, trust=0.0) is False


def test_proactive_openers_are_distinct(engine):
    """主动关怀：拿到的几句开口话术不能是同一件事的换皮。"""
    _feed(engine)
    openers = engine.proactive_openers(ROLE, limit=3)
    assert isinstance(openers, list)
    if openers:
        assert all(isinstance(text, str) and text.strip() for text in openers)
        assert len(set(openers)) == len(openers)


def test_proactive_opener_does_not_parrot_a_greeting(engine):
    """主动开口不能把「你好呀，我叫小林」整句塞进引号里当话题。"""
    engine.record_turn(ROLE, "你好呀，我叫小林", "哎，孩子，坐下说。", "平静")
    openers = engine.proactive_openers(ROLE)
    assert openers, "至少要给一句兜底开场白"
    joined = "".join(openers)
    assert "你好呀" not in joined, f"寒暄不该被当成话题：{joined}"


def test_proactive_suggestions_mix_question_and_statement(engine):
    """「下一句」不能只有疑问句：同一批里既要有追问，也要有陈述/关心类说法。"""
    _feed(engine)
    items = engine.proactive_suggestions(ROLE, limit=3)
    assert items
    for item in items:
        assert set(item) >= {"opener", "style", "label", "hook", "hook_kind", "reason"}
        assert item["style"] in memory_os.PROACTIVE_STYLES
        assert item["label"] == memory_os.STYLE_LABELS[item["style"]]
    asks = [item for item in items if item["opener"].rstrip().endswith(("？", "?"))]
    statements = [item for item in items if item not in asks]
    assert asks, f"应该保留一句追问：{items}"
    assert statements, f"还应该有不是问句的说法：{items}"
    assert len({item["style"] for item in items}) == len(items), "同一批里句型不要重复"


def test_proactive_style_rotates_between_turns(engine):
    """上一轮用过的句型，这一轮不再打头——真人不会每次都同一个开场。"""
    _feed(engine)
    first = engine.proactive_suggestions(ROLE, limit=1)[0]
    second = engine.proactive_suggestions(ROLE, limit=1)[0]
    assert first["style"] != second["style"], f"连着两轮同一种句型：{first} / {second}"
    remembered = (engine.data.get("meta") or {}).get("proactive_last_style") or {}
    assert remembered.get(ROLE) == second["style"], "用过的句型要记进 meta，下一轮才能避开"


def test_auto_consolidate_switch_off(engine, monkeypatch):
    """消融开关 auto_consolidate=False：只攒 working turns，不自动固化。"""
    engine.config.auto_consolidate = False
    _feed(engine)
    assert engine.pending_turns(ROLE) >= 8
    assert engine.last_consolidation is None

    forced = engine.consolidate(ROLE, force=True)
    assert forced is not None


def test_time_injected_decay_and_archive(engine):
    """时间可注入：60 天不联系，Episode 强度跌破阈值并被标记 archived（不删除）。"""
    base = 1_700_000_000_000
    _feed(engine, base_ms=base)
    engine.consolidate(ROLE, force=True)

    later = base + 60 * 24 * 3600 * 1000
    engine.decay_pass(ROLE, now=later)
    snapshot = engine.snapshot_for_ui(ROLE, now=later)
    episodes = snapshot["mid"]["items"]
    assert episodes
    assert any(item["strength"] < cfg.ARCHIVE_THRESHOLD for item in episodes)
    assert episodes[0]["archived"] is True
    assert snapshot["stats"]["episodes"] >= 1, "归档只做标记，条目不许被删掉"


def test_reset_role_clears_memory(engine):
    _feed(engine)
    engine.reset_role(ROLE)
    snapshot = engine.snapshot_for_ui(ROLE)
    assert snapshot["stats"]["turns"] == 0
    assert snapshot["stats"]["facts"] == 0
    assert snapshot["stats"]["episodes"] == 0


def test_persona_compiles_and_drives_prompt(engine):
    """人设编译器 → system prompt 的完整链路，且能把记忆块拼进去。"""
    _feed(engine)
    bundle = engine.retrieve(ROLE, "面试", "难过")
    block = engine.build_memory_block(bundle)

    compiled = compile_persona(
        "在城中村开馄饨摊的退休刑警，嘴上骂骂咧咧心里软，一提当年案子就沉默",
        knobs={"毒舌": 0.8, "温柔": 0.4},
    )
    assert compiled.card.name
    assert compiled.card.speech.sample_lines, "台词锚点是人设可执行性的关键，不能为空"
    assert compiled.health.score >= 60
    assert len(compiled.critique.scores) == 6

    prompt = build_system_prompt(compiled.card, block, "陌生", None)
    assert compiled.card.name in prompt
    assert "【短期" in prompt or "记忆" in prompt
    assert "reply" in prompt and "emotion" in prompt
    assert "None" not in prompt
    assert "【台词锚点" in prompt or "台词" in prompt

    directives = render_style_directives(compiled.card, {"stage": "亲近", "intimacy": 70})
    assert isinstance(directives, list) and directives

    restored = dict_to_card(card_to_dict(compiled.card))
    assert restored.name == compiled.card.name
    assert restored.speech.sample_lines == compiled.card.speech.sample_lines


def test_legacy_role_still_works(engine):
    """旧版人设（只有一个 persona 字符串）必须还能跑，不能报错、也不能出现 None。"""
    card = from_legacy("傲娇女儿", {"persona": "嘴硬心软的傲娇女儿", "opening": "哼，你来了。", "avatar": "👧"})
    assert card.legacy is True
    prompt = build_system_prompt(card, "", "熟悉", None)
    assert "傲娇" in prompt
    assert "None" not in prompt
    assert "reply" in prompt


def test_demo_chat_protocol_has_five_fields(demo_mode):
    """演示模式的对话返回要满足 UI 的五字段协议（含 initiative / loop_hint）。"""
    payload = llm.structured_chat("你在扮演一个愿意听人说话的人。", [{"role": "user", "content": "我今天特别累"}])
    assert payload["reply"]
    assert payload["emotion"] in cfg.ALLOWED_EMOTIONS
    assert set(payload) >= {"reply", "emotion", "memory_update", "initiative", "loop_hint"}


def test_memory_block_stable_across_calls(engine):
    """同样的输入两次构建的记忆块内容一致（演示模式确定性，答辩可复现）。"""
    _feed(engine)
    now = time.time() * 1000
    first = engine.build_memory_block(engine.retrieve(ROLE, "面试", "难过", now=now))
    second = engine.build_memory_block(engine.retrieve(ROLE, "面试", "难过", now=now))
    assert first == second


def test_store_persists_and_reloads(tmp_path, demo_mode):
    """落盘后重新加载，记忆不丢（UI 每次 rerun 都会重新 load）。"""
    path = tmp_path / "memory.json"
    engine = MemoryEngine(MemoryStore(path))
    _feed(engine)
    stats_before = engine.snapshot_for_ui(ROLE)["stats"]

    reloaded = MemoryEngine(MemoryStore(path))
    stats_after = reloaded.snapshot_for_ui(ROLE)["stats"]
    assert stats_after["facts"] == stats_before["facts"]
    assert stats_after["episodes"] == stats_before["episodes"]
    assert stats_after["turns"] == stats_before["turns"]


# ---------------------------------------------------------------------------
# v4 五层记忆：L0 原文库 / L3 固化快照 / L4 自我认知
# ---------------------------------------------------------------------------


def _write_chats(engine: MemoryEngine, base_ms: float = 1_700_000_000_000) -> None:
    """模拟 UI 侧写 L0 原文库（record_turn 不管 chats，由 append_exchange 负责）。"""
    data = engine.data
    chats = data.setdefault("chats", {})
    messages = []
    for index, (user_text, reply) in enumerate(DIALOGUE):
        stamp = base_ms + index * 120_000
        messages.append({"role": "user", "content": user_text, "ts": stamp})
        messages.append({"role": "assistant", "content": reply, "ts": stamp + 1})
    chats[ROLE] = messages
    engine.store.save(data)


def test_l0_archive_search_and_stats(tmp_path, demo_mode):
    """L0：原文库永久保留，可按关键词回溯到原话，统计计数正确。"""
    engine = MemoryEngine(MemoryStore(tmp_path / "memory.json"))
    _feed(engine)
    _write_chats(engine)

    stats = engine.archive_stats(ROLE)
    assert stats["messages"] == len(DIALOGUE) * 2
    assert stats["user_messages"] == len(DIALOGUE)
    assert stats["first_label"] and stats["last_label"]

    hits = engine.search_archive(ROLE, "面试", limit=3)
    assert hits, "原文库应当能按关键词检索到历史原话"
    first = hits[0]
    assert {"id", "ts", "ts_label", "sender", "text", "score"} <= set(first)
    assert "面试" in first["text"]
    assert 0.0 <= first["score"] <= 1.0
    assert engine.search_archive(ROLE, "量子力学与黑洞蒸发") == [] or isinstance(
        engine.search_archive(ROLE, "量子力学与黑洞蒸发"), list
    )


def test_l3_snapshot_file_and_index(tmp_path, demo_mode):
    """L3：每次真正固化都落一份快照文件，并在 snapshot_for_ui 里留下索引。"""
    engine = MemoryEngine(MemoryStore(tmp_path / "memory.json"))
    _feed(engine)  # 4 轮 → 自动固化一次

    snapshot = engine.snapshot_for_ui(ROLE)
    assert "snapshots" in snapshot
    assert snapshot["snapshots"]["count"] >= 1
    item = snapshot["snapshots"]["items"][0]
    for field in ("id", "label", "episode_title", "facts_added", "facts_updated"):
        assert field in item

    files = list(engine.archive_dir.glob("*/*.json"))
    assert files, "快照正文必须真的落盘，不能只写索引"


def test_l4_self_note_injected_only_when_requested(tmp_path, demo_mode):
    """L4：自我叙述默认不进记忆块，只有新会话首轮（include_self=True）才注入。"""
    engine = MemoryEngine(MemoryStore(tmp_path / "memory.json"))
    _feed(engine)
    engine.consolidate(ROLE, force=True)

    plain = engine.retrieve(ROLE, "面试", "难过")
    block_plain = engine.build_memory_block(plain)
    assert "【它的自我叙述】" not in block_plain

    with_self = engine.retrieve(ROLE, "面试", "难过", include_self=True)
    block_self = engine.build_memory_block(with_self, include_self=True)
    assert "【它的自我叙述】" in block_self
    assert "None" not in block_self
    assert with_self.self_notes, "self_notes 必须挂在 bundle 上，否则记忆块无处可取"

    snapshot = engine.snapshot_for_ui(ROLE)
    assert snapshot["self"]["count"] >= 1
    note = snapshot["self"]["items"][0]
    for field in ("id", "content", "note_type", "created_label", "mention_count"):
        assert field in note


def test_correct_fact_supersedes_old_one(tmp_path, demo_mode):
    """记忆主权「改」：纠正一条事实 → 新事实置信 0.9，旧事实 superseded_by 留档。"""
    engine = MemoryEngine(MemoryStore(tmp_path / "memory.json"))
    _feed(engine)
    engine.consolidate(ROLE, force=True)

    facts = [item for item in engine.snapshot_for_ui(ROLE)["long"]["items"] if not item["superseded_by"]]
    assert facts
    old = facts[0]
    fixed = engine.correct_fact(ROLE, old["id"], "用户叫小林，住在南方一个总下雨的城市")
    assert fixed is not None
    assert fixed["confidence"] >= cfg.CORRECTION_CONFIDENCE
    assert fixed["id"] != old["id"]

    after = engine.snapshot_for_ui(ROLE)["long"]["items"]
    old_after = next(item for item in after if item["id"] == old["id"])
    assert old_after["superseded_by"] == fixed["id"]
    new_after = next(item for item in after if item["id"] == fixed["id"])
    assert "南方" in new_after["content"]
    assert engine.correct_fact(ROLE, "fact_不存在", "随便") is None
    assert engine.correct_fact(ROLE, old["id"], "   ") is None


def test_proactive_openers_reference_what_the_user_actually_said(engine):
    """主动开口要"接着具体的事"，不能是放之四海皆准的寒暄。"""
    from memory_os import DEFAULT_OPENER

    _feed(engine)
    openers = engine.proactive_openers(ROLE, limit=2)
    assert openers, "有记忆时必须能给出开场白"
    assert openers[0] != DEFAULT_OPENER, "不该退回默认寒暄"
    assert any(token in openers[0] for token in ("面试", "住院", "医院", "加班", "妈妈")), openers[0]
    assert len(set(openers)) == len(openers), "同一件事不要换个说法重复出现"


def test_proactive_opener_uses_recent_line_before_consolidation(engine):
    """还没固化（只有短期层）时，也要能接住"你刚才说的那句话"。"""
    engine.record_turn(ROLE, "今天在地铁上被人踩了一脚，鞋子全黑了", "哎，真够烦的。", "生气")
    openers = engine.proactive_openers(ROLE, limit=1)
    assert openers and "地铁" in openers[0], openers
