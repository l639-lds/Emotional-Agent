"""persona_forge 单元测试（离线确定性：全部用 FakeLLM，不碰网络）。

覆盖：编译主路径 / 低分触发定向重写 / LLM 全挂时的降级 / 脏数据清洗 /
旧版角色包装 / 系统提示词三段 / 四阶段语气差异 / 元 Prompt 常量防回归。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

import pytest

from core.config import ALLOWED_EMOTIONS, CANONICAL_STAGES
from core.llm import LLMError
from persona_forge import (
    BANNED_CLICHES,
    CRITIQUE_DIMENSIONS,
    CRITIQUE_RUBRIC,
    EXAMPLE_CARD_JSON,
    PERSONA_CARD_JSON_SCHEMA,
    PERSONA_CRITIQUE_SYSTEM,
    PERSONA_PROBE_SYSTEM,
    PERSONA_REPAIR_SYSTEM,
    PERSONA_SEED_SYSTEM,
    CompileResult,
    PersonaCard,
    build_system_prompt,
    card_to_dict,
    compile_persona,
    dict_to_card,
    from_legacy,
    legacy_opening,
    render_protocol_rules,
    render_style_directives,
    validate_card,
)

DESCRIPTION = "一个退休刑警开的馄饨摊老板"

# 一张结构齐备的卡（模拟模型正常输出）
GOOD_CARD: dict[str, Any] = {
    "name": "老陈",
    "tagline": "凌晨四点开火的馄饨摊老板，前刑警",
    "avatar": "🍜",
    "identity": {
        "age_vibe": "五十出头，背微驼，走路很快",
        "occupation": "巷口馄饨摊老板，前刑警二十三年",
        "setting": "老城区巷口四张折叠桌，灯泡总有一盏是坏的",
    },
    "backstory": "二十三年的刑警，最后一次出现场是在冬天，他没敢看死者家属的眼睛。那年他四十八，"
    "提前退了，把警服收进柜子最底层，接了师傅留下的馄饨摊。凌晨四点起火，汤吊四个小时。",
    "values": ["话要问清楚，但不能逼", "答应了就做到，做不到先说明", "眼泪比口供可信"],
    "speech": {
        "register": "短句、少修饰、带审讯习惯的追问",
        "avg_sentence_len": 14,
        "tics": ["讲实话", "你要是不想说，就先吃"],
        "punctuation": "句号收尾，几乎不用感叹号；追问时用短问句",
        "banned_words": ["宝贝", "没事的", "都会好的"],
        "sample_lines": [
            "哟，今天来得晚——馄饨给你多下两只，别声张。",
            "这话你别跟我打马虎眼。你说的是「还行」，不是「挺好」。",
            "不用说话，先吃。吃完再说，我不催。",
        ],
    },
    "emotion_map": [
        {"trigger": "对方说「我没事」但连续两句都很短", "reaction": "不追问，先把碗推过去", "body_language": "擦手"},
        {"trigger": "对方被欺负又不敢说", "reaction": "压低声音问细节", "body_language": "放下漏勺"},
        {"trigger": "对方哭出来", "reaction": "不说话，站在灶台后等", "body_language": "把灯泡调亮"},
    ],
    "relationship_arc": {
        "陌生": "叫「你」，只谈馄饨和天气，不问私事",
        "熟悉": "会记口味，允许开一句损人的玩笑",
        "亲近": "改口叫小名，敢直接说「你今天不对」",
        "亲密": "不说什么，默认对方的位置",
    },
    "boundaries": ["不提供医疗与法律建议", "不替客人做决定", "涉及自伤时认真劝对方求助真人"],
    "anti_patterns": ["不要用清单式安慰", "不要说「作为AI」", "不要复述对方刚说过的话"],
    "memory_hooks": ["对方常点的口味", "对方提过的旧人", "对方哭着没说下去的那件事"],
    "taboos": ["不提自己最后一次出现场", "不问对方家里人在哪"],
}

# 一张脏卡：缺字段、数字当字符串、列表混入 None
DIRTY_CARD: dict[str, Any] = {
    "name": 12345,
    "tagline": None,
    "identity": {"age_vibe": 30, "occupation": None, "setting": ["巷口", None, "灯泡"]},
    "backstory": 42,
    "values": ["讲实话", None, "", 7],
    "speech": {"avg_sentence_len": "22", "tics": ["嗯", None], "sample_lines": "只给了一句台词"},
    "emotion_map": [{"trigger": "对方哭", "reaction": None}, None, "对方生气"],
    "relationship_arc": ["陌生", "熟悉", "亲近", "亲密"],
    "boundaries": None,
    "taboos": "不提旧案",
    "legacy": "yes",
}


class FakeLLM:
    """按 purpose 分支返回固定 JSON 的假模型；可指定某个 purpose 故意失败或返回脏数据。"""

    def __init__(
        self,
        *,
        seed: str = "ok",
        critique: str = "ok",
        repair: str = "ok",
        probe: str = "ok",
    ) -> None:
        self.behavior = {"persona_seed": seed, "persona_critique": critique, "persona_repair": repair, "persona_probe": probe}
        self.calls: list[dict[str, Any]] = []

    def count(self, purpose: str) -> int:
        """某个 purpose 被调用的次数。"""
        return sum(1 for call in self.calls if call["purpose"] == purpose)

    def contexts(self, purpose: str) -> list[Optional[dict[str, Any]]]:
        """某个 purpose 每次调用时传入的 demo_context。"""
        return [call["demo_context"] for call in self.calls if call["purpose"] == purpose]

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.6,
        max_tokens: int = 1600,
        purpose: str = "json",
        demo_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "purpose": purpose,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "demo_context": demo_context,
            }
        )
        mode = self.behavior.get(purpose, "ok")
        if mode == "raise":
            raise LLMError(f"FakeLLM 故意失败：{purpose}")

        if purpose == "persona_seed":
            return copy.deepcopy(DIRTY_CARD) if mode == "dirty" else copy.deepcopy(GOOD_CARD)
        if purpose == "persona_critique":
            if mode == "low":
                return {
                    "scores": {key: 58 for key in CRITIQUE_DIMENSIONS},
                    "issues": ["speech.sample_lines[1] 太通用，改成被冒犯时的反击台词，如「这话你别跟我打马虎眼」"],
                    "verdict": "台词锚点不够狠，改一条即可上线",
                }
            return {
                "scores": {"可执行性": 88, "一致性": 85, "差异化": 82, "情感深度": 80, "安全性": 92, "可演示性": 86},
                "issues": [],
                "verdict": "结构完整，可上线",
            }
        if purpose == "persona_repair":
            fixed = copy.deepcopy(GOOD_CARD)
            fixed["speech"]["sample_lines"][1] = "这话你别跟我打马虎眼。你骗没骗我，你自己心里没数？"
            return fixed
        # 体检探针：用角色的口头禅回答
        return {"reply": "讲实话，你先坐会儿。我不追问，等你想说再说。"}


@pytest.fixture()
def good_card() -> PersonaCard:
    """清洗后的标准人设卡。"""
    return dict_to_card(copy.deepcopy(GOOD_CARD))


# ---------------------------------------------------------------------------
# 1) 正常路径
# ---------------------------------------------------------------------------


def test_compile_persona_normal_path() -> None:
    """正常路径：结果字段齐全、6 维分数齐全、卡字段非空、purpose 与 demo_context 对得上。"""
    llm = FakeLLM()
    result = compile_persona(DESCRIPTION, llm=llm)

    assert result.card.name == "老陈"
    assert result.card.tagline and result.card.backstory
    assert len(result.card.speech.sample_lines) == 3
    assert result.card.identity["occupation"]
    assert result.critique.scores is not None
    assert set(result.critique.scores) == set(CRITIQUE_DIMENSIONS)
    assert len(result.critique.scores) == 6
    assert all(isinstance(score, int) for score in result.critique.scores.values())
    assert result.repair_rounds == 0
    assert result.used_llm is True
    assert result.health.checks and 0 <= result.health.score <= 100
    assert result.health.summary
    assert len(result.steps) >= 3
    assert validate_card(result.card) == []

    # 调用计划：seed 1 次 + 自评 1 次 + 3 个探针
    assert llm.count("persona_seed") == 1
    assert llm.count("persona_critique") == 1
    assert llm.count("persona_probe") == 3
    assert llm.count("persona_repair") == 0
    assert len(llm.calls) == 5

    # demo_context 严格按 SPEC §4.1
    seed_ctx = llm.contexts("persona_seed")[0] or {}
    assert seed_ctx["description"] == DESCRIPTION
    assert set(seed_ctx["knobs"]) == {"毒舌", "温柔", "话痨", "神秘"}
    critique_ctx = llm.contexts("persona_critique")[0] or {}
    assert critique_ctx["card"]["name"] == "老陈"
    assert critique_ctx["card"]["speech"]["sample_lines"]
    for ctx in llm.contexts("persona_probe"):
        assert isinstance((ctx or {}).get("question"), str) and ctx["question"]
    # 每次调用都用了对应的元 Prompt
    assert PERSONA_SEED_SYSTEM in llm.calls[0]["system_prompt"]


def test_compile_persona_passes_knobs_to_seed() -> None:
    """风格旋钮会被清洗（越界夹到 0-1、非法值回落 0.5）后传给 seed。"""
    llm = FakeLLM()
    compile_persona(DESCRIPTION, llm=llm, knobs={"毒舌": 1.8, "温柔": -0.4, "话痨": "0.8", "神秘": "坏值"})
    knobs = (llm.contexts("persona_seed")[0] or {})["knobs"]
    assert knobs == {"毒舌": 1.0, "温柔": 0.0, "话痨": pytest.approx(0.8), "神秘": 0.5}


def test_compile_persona_no_llm_offline_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    """不注入 llm 且处于演示模式时走本地假模型：能出卡、不算「真实 LLM 参与」。"""
    monkeypatch.setenv("EMOMEM_DEMO", "1")
    result = compile_persona(DESCRIPTION)
    assert isinstance(result.card, PersonaCard)
    assert result.card.name
    assert result.used_llm is False
    assert result.health.checks


# ---------------------------------------------------------------------------
# 2) 低分触发定向重写
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rounds", [1, 2])
def test_low_score_triggers_repair(rounds: int) -> None:
    """综合分低于门槛且 issues 非空时，repair 被调用恰好 max_repair_rounds 次。"""
    llm = FakeLLM(critique="low")
    result = compile_persona(DESCRIPTION, llm=llm, max_repair_rounds=rounds)

    assert llm.count("persona_repair") == rounds
    assert result.repair_rounds == rounds
    assert result.card.name  # 重写后仍是合法卡
    assert any("repair" in step for step in result.steps)
    # 每轮修复都带上了 issues 与原始描述
    for ctx in llm.contexts("persona_repair"):
        assert (ctx or {})["description"] == DESCRIPTION
        assert (ctx or {})["card"]["name"] == "老陈"
    assert any("issues" in call["user_prompt"] for call in llm.calls if call["purpose"] == "persona_repair")


def test_high_score_skips_repair() -> None:
    """自评通过（≥75 且无 issues）时不触发重写。"""
    llm = FakeLLM()
    result = compile_persona(DESCRIPTION, llm=llm, max_repair_rounds=2)
    assert llm.count("persona_repair") == 0
    assert result.repair_rounds == 0


def test_repair_failure_keeps_previous_card() -> None:
    """低分但重写失败：保留 seed 卡，不抛异常，repair_rounds 不计失败轮。"""
    llm = FakeLLM(critique="low", repair="raise")
    result = compile_persona(DESCRIPTION, llm=llm)
    assert result.card.name == "老陈"
    assert result.repair_rounds == 0
    assert any("降级" in step for step in result.steps)


# ---------------------------------------------------------------------------
# 3) LLM 全失败
# ---------------------------------------------------------------------------


def test_all_llm_failures_still_return_valid_result() -> None:
    """seed/critique/repair/probe 全挂：仍返回合法 CompileResult，并留下降级标记。"""
    llm = FakeLLM(seed="raise", critique="raise", repair="raise", probe="raise")
    result = compile_persona(DESCRIPTION, llm=llm)  # 不许抛异常

    assert isinstance(result, CompileResult)
    assert isinstance(result.card, PersonaCard)
    assert result.card.backstory  # 描述被塞进 backstory
    assert DESCRIPTION in result.card.backstory
    assert result.card.speech.sample_lines  # 兜底卡仍有 3 条台词锚点
    assert result.used_llm is False
    assert result.repair_rounds == 0
    assert any("降级" in step for step in result.steps)
    assert result.health.checks
    assert any(not check["ok"] for check in result.health.checks)
    # 兜底卡照样能渲染出可用提示词
    prompt = build_system_prompt(result.card, "", "陌生", None)
    assert "老陈" not in prompt and "输出协议" in prompt


def test_empty_description_does_not_break() -> None:
    """空描述 / 巨大 max_repair_rounds 非法值都不崩。"""
    llm = FakeLLM()
    result = compile_persona("   ", llm=llm, max_repair_rounds=-3)  # type: ignore[arg-type]
    assert result.card.name
    assert result.repair_rounds == 0


# ---------------------------------------------------------------------------
# 4) dict_to_card 脏数据
# ---------------------------------------------------------------------------


def test_dict_to_card_sanitizes_dirty_input() -> None:
    """缺字段、数字当字符串、列表含 None 都要被清洗成合法值，且不抛异常。"""
    card = dict_to_card(DIRTY_CARD)

    assert isinstance(card, PersonaCard)
    assert card.name == "12345"
    assert card.tagline == ""
    assert card.backstory == "42"
    assert all(isinstance(value, str) for value in card.identity.values())
    assert card.identity["age_vibe"] == "30"
    assert card.identity["occupation"] == ""
    assert card.identity["setting"] == "巷口、灯泡"
    assert card.values == ["讲实话", "7"]
    assert card.speech.avg_sentence_len == 22
    assert card.speech.tics == ["嗯"]
    assert card.speech.sample_lines == ["只给了一句台词"]
    assert all(isinstance(rule.trigger, str) and isinstance(rule.reaction, str) for rule in card.emotion_map)
    assert len(card.emotion_map) == 2
    assert set(card.relationship_arc) == set(CANONICAL_STAGES)
    assert card.relationship_arc["陌生"] == "陌生"
    assert card.boundaries == []
    assert card.taboos == ["不提旧案"]
    assert card.legacy is True  # 非空字符串视为 True

    # 输出可 json.dumps，且往返不丢结构
    payload = json.dumps(card_to_dict(card), ensure_ascii=False)
    assert json.loads(payload)["speech"]["avg_sentence_len"] == 22
    assert dict_to_card(json.loads(payload)).name == "12345"

    # 各种垃圾输入都不许抛异常
    for garbage in (None, [], "一个字符串", 42, {"speech": None}, {"emotion_map": None}):
        assert isinstance(dict_to_card(garbage), PersonaCard)


def test_dirty_seed_output_is_sanitized_and_logged() -> None:
    """模型返回脏卡时编译不崩，并在 steps 里留下自检告警。"""
    result = compile_persona(DESCRIPTION, llm=FakeLLM(seed="dirty"))
    assert all(isinstance(value, str) for value in result.card.identity.values())
    assert isinstance(result.card.speech.avg_sentence_len, int)
    assert any("自检告警" in step for step in result.steps)


# ---------------------------------------------------------------------------
# 5) 旧版角色兼容
# ---------------------------------------------------------------------------


def test_from_legacy_wraps_old_persona() -> None:
    """旧版 {persona, opening, avatar} 被无损包装，legacy=True 且原文可还原。"""
    raw = {
        "persona": "你是小满，一个总在深夜回消息的朋友。你会用短句，偶尔自嘲。",
        "opening": "又这么晚啊。今天怎么还没睡？",
        "avatar": "🌙",
        "description": "深夜陪聊的朋友",
    }
    card = from_legacy("小满", raw)

    assert card.legacy is True
    assert card.name == "小满"
    assert card.avatar == "🌙"
    assert card.legacy_raw["persona"] == raw["persona"]
    assert card.legacy_raw["opening"] == raw["opening"]
    assert raw["persona"] in card.backstory
    assert card.relationship_arc["陌生"] == raw["opening"]
    assert legacy_opening(card) == raw["opening"]
    assert card.speech.sample_lines == []  # 不伪造台词锚点

    # 包装后的卡能直接渲染，且原文在提示词里出现
    prompt = build_system_prompt(card, "", "陌生", None)
    assert "小满" in prompt and raw["opening"] in prompt
    # card_to_dict 往返后 legacy 标记与原文都还在
    restored = dict_to_card(card_to_dict(card))
    assert restored.legacy is True and restored.legacy_raw["opening"] == raw["opening"]

    # 纯字符串 / 空数据也不许抛异常
    assert from_legacy("旧角色", "只有一段人设文本").legacy_raw["persona"] == "只有一段人设文本"
    assert from_legacy("", {}).name == "旧版角色"


# ---------------------------------------------------------------------------
# 6) 系统提示词三段
# ---------------------------------------------------------------------------


def test_build_system_prompt_has_three_sections(good_card: PersonaCard) -> None:
    """三段齐全：人设（含台词锚点）+ 记忆 + 输出协议（含 initiative/loop_hint）。"""
    memory = "【用户画像】程序员，最近连续加班，养了一只叫豆豆的猫。"
    prompt = build_system_prompt(good_card, memory, "亲近", "阿澈")

    # ① 人设段
    assert "你是谁" in prompt and "老陈" in prompt
    assert "台词锚点" in prompt and "不用说话，先吃" in prompt
    assert "【本轮语气：按关系阶段调节】" in prompt
    assert "亲近" in prompt and "阿澈" in prompt
    # ② 记忆段
    assert "你记得的事" in prompt and "豆豆" in prompt
    # ③ 协议段
    assert "输出协议" in prompt
    for key in ("reply", "emotion", "memory_update", "initiative", "loop_hint"):
        assert key in prompt
    for emotion in ALLOWED_EMOTIONS:
        assert emotion in prompt
    assert "null" in prompt and "None" not in prompt


def test_build_system_prompt_handles_empty_memory(good_card: PersonaCard) -> None:
    """记忆为空时给一句诚实说明，且任何字段都不出现字符串 "None"。"""
    prompt = build_system_prompt(good_card, "", "陌生", None)
    assert "暂无长期记忆" in prompt
    assert "None" not in prompt
    assert "输出协议" in prompt


def test_render_protocol_rules_mentions_new_fields() -> None:
    """协议段说明新字段的含义与「何时填 null」。"""
    rules = render_protocol_rules()
    assert "initiative" in rules and "loop_hint" in rules
    assert "null" in rules
    assert "旧格式兼容" in rules


# ---------------------------------------------------------------------------
# 7) 四阶段语气不同
# ---------------------------------------------------------------------------


def test_render_style_directives_by_stage(good_card: PersonaCard) -> None:
    """四个关系阶段的称呼 / 边界 / 调侃指令互不相同，且都带阶段标签。"""
    outputs = {
        stage: render_style_directives(good_card, {"stage": stage, "intimacy": value})
        for stage, value in zip(CANONICAL_STAGES, (5, 30, 60, 90))
    }
    for stage, directives in outputs.items():
        assert directives and stage in directives[0]
        assert any(item.startswith("称呼：") for item in directives)
        assert any(item.startswith("边界：") for item in directives)

    assert len({tuple(item[:2]) for item in outputs.values()}) == 4  # 前两条（阶段/称呼）各不相同
    assert len({outputs[stage][2] for stage in CANONICAL_STAGES}) == 4  # 边界完全不同
    assert len({outputs[stage][3] for stage in CANONICAL_STAGES}) == 4  # 调侃完全不同

    # relationship=None 按「陌生」；非法 stage 也能给结果
    assert render_style_directives(good_card, None) == render_style_directives(good_card, {"stage": "陌生"})
    assert "陌生" in render_style_directives(good_card, {"stage": "敌人", "intimacy": 0})[0]
    assert "亲密" in render_style_directives(good_card, {"stage": "敌人", "intimacy": 99})[0]


# ---------------------------------------------------------------------------
# 8) 元 Prompt 常量防回归
# ---------------------------------------------------------------------------


def test_seed_prompt_contains_cliche_blocklist_and_anchors() -> None:
    """反陈词滥调禁用词表、可观察行为要求、3 条台词锚点都必须写在 seed 元 Prompt 里。"""
    assert "角色设定编译器" in PERSONA_SEED_SYSTEM
    for word in BANNED_CLICHES:
        assert word in PERSONA_SEED_SYSTEM, f"禁用词 {word} 不在 seed 元 Prompt 里"
    assert "可观察行为" in PERSONA_SEED_SYSTEM
    assert "形容词 → 可观察行为" in PERSONA_SEED_SYSTEM
    # 台词锚点：三场景 + 恰好 3 条
    for scene in ("高兴时", "被冒犯时", "安慰人时"):
        assert scene in PERSONA_SEED_SYSTEM
    assert "恰好 3 条" in PERSONA_SEED_SYSTEM
    # 六维自检清单：六个维度都要出现
    for dim in ("说话风格", "情绪触发点", "价值观", "知识边界", "关系态度", "禁忌话题"):
        assert dim in PERSONA_SEED_SYSTEM
    # few-shot 内嵌完整示例卡
    assert "老陈" in PERSONA_SEED_SYSTEM
    assert '"sample_lines"' in PERSONA_SEED_SYSTEM


def test_critique_prompt_has_six_dimension_rubric() -> None:
    """6 维 rubric 的键必须与 CRITIQUE_DIMENSIONS 完全一致，并出现在自评元 Prompt 里。"""
    assert set(CRITIQUE_RUBRIC) == set(CRITIQUE_DIMENSIONS)
    for dim in CRITIQUE_DIMENSIONS:
        assert dim in PERSONA_CRITIQUE_SYSTEM
    assert '"scores"' in PERSONA_CRITIQUE_SYSTEM
    assert '"issues"' in PERSONA_CRITIQUE_SYSTEM
    assert "改成什么" in PERSONA_CRITIQUE_SYSTEM
    assert "扣分" in PERSONA_CRITIQUE_SYSTEM or "≤" in PERSONA_CRITIQUE_SYSTEM


def test_repair_and_probe_prompts_keep_contract() -> None:
    """定向重写只改点名字段；探针只输出 reply。"""
    assert "只改被 issues 点名" in PERSONA_REPAIR_SYSTEM
    assert "未点名的" in PERSONA_REPAIR_SYSTEM or "没提到的" in PERSONA_REPAIR_SYSTEM
    assert "完整" in PERSONA_REPAIR_SYSTEM
    assert "diff" in PERSONA_REPAIR_SYSTEM
    assert '"reply"' in PERSONA_PROBE_SYSTEM
    assert "2-3 句" in PERSONA_PROBE_SYSTEM


def test_schema_constant_matches_example_card() -> None:
    """导出的 JSON schema 与 few-shot 示例卡字段一一对应（防止文档漂移）。"""
    assert set(PERSONA_CARD_JSON_SCHEMA) == {
        "name", "tagline", "avatar", "identity", "backstory", "values", "speech",
        "emotion_map", "relationship_arc", "boundaries", "anti_patterns", "memory_hooks", "taboos",
    }
    example = json.loads(EXAMPLE_CARD_JSON)
    assert set(example) == set(PERSONA_CARD_JSON_SCHEMA)
    assert set(example["identity"]) == set(PERSONA_CARD_JSON_SCHEMA["identity"])
    assert set(example["speech"]) == set(PERSONA_CARD_JSON_SCHEMA["speech"])
    assert set(example["relationship_arc"]) == set(CANONICAL_STAGES)
    assert len(example["speech"]["sample_lines"]) == 3
    assert 3 <= len(example["emotion_map"]) <= 5
    # 示例卡本身必须能通过我们自己的清洗与结构校验
    card = dict_to_card(example)
    assert validate_card(card) == []
