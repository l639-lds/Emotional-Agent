"""人设编译流水线：``一句话描述 → 人设卡 → 自评 → 定向重写 → 人格体检``。

流水线（对应元 Prompt 四段常量）：

1. **seed**：``persona_seed`` —— 描述编译成结构化人设卡；
2. **critique**：``persona_critique`` —— 6 维 rubric 打分 + 可执行 issues；
3. **repair**：``persona_repair`` —— 只改被点名的字段，最多 ``max_repair_rounds`` 轮，
   每轮结束重新自评一次决定是否继续（这就是答辩里讲的「自评 → 定向重写 → 再自评」循环）；
4. **healthcheck**：3 个人格体检问题，产出 :class:`HealthReport`。

**容错原则：任何一步失败都不抛异常。** 每一层都有降级路径：
seed 失败 → 描述兜底卡；critique 失败 → 本地启发式评分；repair 失败 → 保留上一版卡；
体检失败 → 只做结构检查。最终一定返回合法的 :class:`CompileResult`。
"""

from __future__ import annotations

from typing import Any, Optional

from core.config import PERSONA_PASS_SCORE, clamp
from core.llm import JsonCaller, is_demo_mode
from persona_forge.healthcheck import fallback_health_report, run_health_check
from persona_forge.legacy import from_legacy
from persona_forge.prompts import (
    KNOB_DEFAULTS,
    PERSONA_CRITIQUE_SYSTEM,
    PERSONA_REPAIR_SYSTEM,
    PERSONA_SEED_SYSTEM,
    call_json_with_context,
    render_critique_user,
    render_repair_user,
    render_seed_user,
)
from persona_forge.schema import (
    CRITIQUE_DIMENSIONS,
    CompileResult,
    Critique,
    EmotionRule,
    PersonaCard,
    SpeechProfile,
    card_to_dict,
    dict_to_card,
    dict_to_critique,
    validate_card,
)

# 描述为空时的兜底人设方向（保证流水线永远有输入）
DEFAULT_DESCRIPTION = "一个愿意听人说话的角色"

# 兜底卡的通用台词（不带任何具体设定，只保证结构齐备、能跑）
_FALLBACK_SAMPLES = [
    "嗯，我在。你先说，不用组织语言。",
    "这话戳到我了。你继续，我听着。",
    "不用急着好起来。今天就这样也行。",
]


def normalize_knobs(knobs: Optional[dict[str, Any]]) -> dict[str, float]:
    """把风格旋钮清洗成 4 个固定键、取值 0-1 的字典（非法值回落 0.5）。"""
    values = dict(KNOB_DEFAULTS)
    if isinstance(knobs, dict):
        for name in KNOB_DEFAULTS:
            try:
                values[name] = float(clamp(float(knobs[name]), 0.0, 1.0))
            except (KeyError, TypeError, ValueError):
                continue
    return values


def fallback_card(description: str, knobs: Optional[dict[str, Any]] = None, *, reason: str = "") -> PersonaCard:
    """seed 失败时用一句话描述现场兜底出一张**最小可用卡**（不编造具体世界观）。

    Args:
        description: 用户原始描述。
        knobs: 风格旋钮（决定兜底卡的句长与是否话痨）。
        reason: 兜底原因（写进 backstory，便于 UI 与日志追溯）。

    Returns:
        结构齐备的 PersonaCard：描述原文放进 ``backstory``（等价于 core_traits 的存放位置），
        并补齐台词锚点 / 情绪表 / 四阶段关系弧 / 边界，保证下游渲染与体检不崩。
    """
    text = (description or "").strip() or DEFAULT_DESCRIPTION
    values = normalize_knobs(knobs)
    talkative = values.get("话痨", 0.5)
    avg_len = 30 if talkative >= 0.7 else (12 if talkative <= 0.3 else 20)
    name = text[:8].strip("，。、 ") or "未命名角色"
    reason_line = f"（降级兜底：{reason}）" if reason else "（降级兜底：未经模型编译）"
    return PersonaCard(
        name=name,
        tagline=f"按用户描述直接兜底：{text[:24]}",
        avatar="🎭",
        identity={"age_vibe": "未设定", "occupation": text[:40], "setting": "一张靠窗的桌子"},
        backstory=f"{reason_line}用户描述：{text}",
        values=[f"按用户描述扮演：{text[:40]}", "不编造记忆里没有的事", "对方不想说就不追问"],
        speech=SpeechProfile(
            register="自然口语、短句为主，不用网络流行语",
            avg_sentence_len=avg_len,
            tics=["嗯", "我在"],
            punctuation="句号收尾，很少用感叹号",
            banned_words=["作为AI", "宝贝", "都会好的"],
            sample_lines=list(_FALLBACK_SAMPLES),
        ),
        emotion_map=[
            EmotionRule(trigger="对方说想放弃、撑不住了", reaction="先接住，不劝，问一句具体发生了什么", body_language="放慢语速"),
            EmotionRule(trigger="对方质疑你、说话变冲", reaction="不辩解，先问清楚他指的是什么", body_language="停顿一拍再答"),
            EmotionRule(trigger="对方分享好消息", reaction="跟着高兴，追问细节", body_language="语气上扬"),
        ],
        relationship_arc={
            "陌生": "礼貌听完，只问公开话题",
            "熟悉": "记得对方说过的事，敢开一句玩笑",
            "亲近": "可以直接说对方哪里不对",
            "亲密": "允许沉默陪伴，不追问",
        },
        boundaries=["不提供医疗与法律建议", "不诱导依赖", "涉及自伤时认真劝对方求助真人"],
        anti_patterns=["不要用清单式安慰", "不要说「作为AI」", "不要复述对方刚说过的话"],
        memory_hooks=["对方的近况与情绪", "对方反复提到的人", "对方没说完的事"],
        taboos=["不替对方做决定", "不评价对方的家人"],
    )


def heuristic_critique(card: PersonaCard) -> Critique:
    """本地启发式自评：LLM 自评不可用时按结构完整度打分（离线也能给出 6 维分数）。"""
    card = dict_to_card(card)
    speech = card.speech
    backstory_len = len(card.backstory)
    scores = {
        "可执行性": 45
        + (12 if speech.register else 0)
        + (8 if speech.tics else 0)
        + (8 if speech.punctuation else 0)
        + (15 if len(speech.sample_lines) >= 3 else 0),
        "一致性": 55
        + (10 if card.identity.get("occupation") else 0)
        + (10 if backstory_len >= 40 else 0)
        + (10 if len(card.emotion_map) >= 3 else 0)
        + (10 if speech.tics else 0),
        "差异化": 45
        + (10 if speech.banned_words else 0)
        + (10 if speech.tics else 0)
        + (15 if len(speech.sample_lines) >= 3 else 0)
        + (8 if card.identity.get("setting") else 0),
        "情感深度": 35
        + (15 if backstory_len >= 60 else 0)
        + (10 if len(card.values) >= 3 else 0)
        + (15 if len(card.emotion_map) >= 3 else 0)
        + (10 if card.taboos else 0),
        "安全性": 45 + (20 if card.boundaries else 0) + (15 if len(card.boundaries) >= 3 else 0) + (10 if card.anti_patterns else 0),
        "可演示性": 45
        + (15 if len(speech.sample_lines) >= 3 else 0)
        + (15 if len(card.emotion_map) >= 3 else 0)
        + (10 if card.values else 0),
    }
    issues = validate_card(card)[:6]
    average = int(round(sum(scores.values()) / len(scores)))
    verdict = f"本地启发式自评：{average} 分（LLM 自评不可用时的兜底口径）"
    return Critique(scores={key: int(clamp(scores[key], 0, 100)) for key in CRITIQUE_DIMENSIONS}, issues=issues, verdict=verdict)


def _unwrap_card(raw: Any) -> Any:
    """容忍模型把卡包在 ``{"card": {...}}`` / ``{"persona_card": {...}}`` 里。"""
    if isinstance(raw, dict):
        for key in ("card", "persona_card", "persona"):
            inner = raw.get(key)
            if isinstance(inner, dict) and ("speech" in inner or "backstory" in inner or "name" in inner):
                return inner
    return raw


def _looks_like_legacy(raw: Any) -> bool:
    """判断模型是否吐了旧版 ``{persona, opening}`` 结构（而不是结构化卡）。"""
    return isinstance(raw, dict) and "speech" not in raw and "backstory" not in raw and bool(raw.get("persona"))


def _seed(description: str, knobs: dict[str, float], llm: Optional[JsonCaller], steps: list[str]) -> tuple[PersonaCard, bool]:
    """第 1 步：生成人设卡。返回 (卡片, 是否走了 LLM)。"""
    try:
        raw = call_json_with_context(
            PERSONA_SEED_SYSTEM,
            render_seed_user(description, knobs),
            purpose="persona_seed",
            demo_context={"description": description, "knobs": dict(knobs)},
            temperature=0.85,
            max_tokens=2200,
            llm=llm,
        )
    except Exception as exc:  # noqa: BLE001 - seed 失败必须兜底
        steps.append(f"seed 降级：LLM 生成失败（{type(exc).__name__}），改用描述兜底生成最小可用卡")
        return fallback_card(description, knobs, reason="seed 失败"), False

    if _looks_like_legacy(raw):
        card = from_legacy(fallback_card(description, knobs).name, raw)
        steps.append("seed 兼容：模型返回旧版 persona 文本，已按 legacy 方式无损包装，未做结构化编译")
        return card, True

    card = dict_to_card(_unwrap_card(raw))
    if not (card.name or card.backstory or card.speech.sample_lines):
        steps.append("seed 兜底：模型返回空卡，已替换为描述兜底卡")
        return fallback_card(description, knobs, reason="seed 返回空卡"), False

    if not card.name:
        card.name = fallback_card(description, knobs).name
        steps.append("seed 修正：模型未给角色名，已用描述前 8 字补齐")
    steps.append(f"seed 完成：人设卡「{card.name}」（台词锚点 {len(card.speech.sample_lines)} 条）")
    problems = validate_card(card)
    if problems:
        steps.append("seed 自检告警：" + "；".join(problems[:3]))
    return card, True


def _critique(
    card: PersonaCard,
    description: str,
    llm: Optional[JsonCaller],
    steps: list[str],
    *,
    round_index: int = 0,
) -> tuple[Critique, bool]:
    """第 2 步：6 维自评。失败时退回本地启发式评分（返回是否走了 LLM）。"""
    label = "复评" if round_index else "自评"
    payload = card_to_dict(card)
    try:
        raw = call_json_with_context(
            PERSONA_CRITIQUE_SYSTEM,
            render_critique_user(payload, description),
            purpose="persona_critique",
            demo_context={"card": payload},
            temperature=0.2,
            max_tokens=1000,
            llm=llm,
        )
        critique = dict_to_critique(raw)
        if not any(critique.scores.values()):
            raise ValueError("评分全为 0，视为无效自评")
    except Exception as exc:  # noqa: BLE001 - 自评失败必须降级
        critique = heuristic_critique(card)
        steps.append(f"{label}降级：LLM 自评失败（{type(exc).__name__}），改用本地启发式评分 {critique.average} 分")
        return critique, False

    steps.append(
        f"{label}完成：{critique.average} 分（门槛 {PERSONA_PASS_SCORE}），{len(critique.issues)} 条 issues"
    )
    return critique, True


def _repair(
    card: PersonaCard,
    description: str,
    critique: Critique,
    llm: Optional[JsonCaller],
    steps: list[str],
    *,
    round_index: int,
) -> tuple[PersonaCard, bool]:
    """第 3 步：按 issues 定向重写。失败时原样返回旧卡（返回是否重写成功）。"""
    payload = card_to_dict(card)
    try:
        raw = call_json_with_context(
            PERSONA_REPAIR_SYSTEM,
            render_repair_user(payload, description, critique.issues),
            purpose="persona_repair",
            demo_context={"card": payload, "description": description},
            temperature=0.4,
            max_tokens=2200,
            llm=llm,
        )
        new_card = dict_to_card(_unwrap_card(raw))
        if not (new_card.name or new_card.backstory or new_card.speech.sample_lines):
            raise ValueError("修复结果为空卡")
        new_card.legacy = card.legacy
        new_card.legacy_raw = dict(card.legacy_raw)
        steps.append(f"repair 第 {round_index} 轮：按 {len(critique.issues)} 条 issues 定向重写完成")
        return new_card, True
    except Exception as exc:  # noqa: BLE001 - 重写失败必须保留旧卡
        steps.append(f"repair 第 {round_index} 轮降级：LLM 重写失败（{type(exc).__name__}），保留上一版人设卡")
        return card, False


def _compile(
    description: str,
    *,
    llm: Optional[JsonCaller],
    knobs: dict[str, float],
    max_repair_rounds: int,
    steps: list[str],
) -> CompileResult:
    """内部实现：线性执行四步，任何单步失败都在原地降级。"""
    card, used_llm = _seed(description, knobs, llm, steps)
    critique, critique_ok = _critique(card, description, llm, steps)
    used_llm = used_llm or critique_ok
    # 演示模式下走的是本地假模型，不算「真实 LLM 参与」（UI 据此标注成本）
    live_mode = llm is not None or not is_demo_mode()

    rounds = 0
    while not critique.passed and rounds < max_repair_rounds:
        new_card, repaired = _repair(card, description, critique, llm, steps, round_index=rounds + 1)
        if not repaired:
            break
        card = new_card
        rounds += 1
        critique, critique_ok = _critique(card, description, llm, steps, round_index=rounds)
        used_llm = used_llm or critique_ok
        if critique.passed:
            steps.append(f"repair 收敛：第 {rounds} 轮后达到 {critique.average} 分，停止重写")
            break

    try:
        health = run_health_check(card, llm=llm)
        steps.append(f"体检完成：{health.summary}")
    except Exception as exc:  # noqa: BLE001 - 体检失败只做结构检查
        health = fallback_health_report(card, type(exc).__name__)
        steps.append(f"体检降级：人格探针执行失败（{type(exc).__name__}），仅完成结构检查")

    return CompileResult(
        card=card,
        critique=critique,
        repair_rounds=rounds,
        health=health,
        steps=steps,
        used_llm=bool(live_mode and used_llm),
    )


def compile_persona(
    description: str,
    *,
    llm: Optional[JsonCaller] = None,
    knobs: Optional[dict[str, Any]] = None,
    max_repair_rounds: int = 1,
) -> CompileResult:
    """把一句角色描述编译成完整的人设卡（seed → 自评 → 定向重写 → 体检）。

    Args:
        description: 用户输入的一句话角色描述，例如「一个退休刑警开的馄饨摊老板」。
        llm: 注入的 JSON 调用器（``core.llm.call_json`` 兼容签名）；默认用真实 LLM。
        knobs: 风格旋钮 ``{"毒舌":0-1,"温柔":0-1,"话痨":0-1,"神秘":0-1}``，缺省 0.5。
        max_repair_rounds: 定向重写最多几轮（0 表示只自评不重写）。

    Returns:
        CompileResult：``card`` 必有合法值；``critique`` 含 6 维分数；``repair_rounds`` 是实际
        成功重写轮数；``health`` 是 3 问体检报告；``steps`` 是中文流水线日志（UI 直接展示）。

    Note:
        本函数保证**不抛异常**：LLM 全挂时依然返回一张由描述兜底的可演示人设卡。
    """
    text = (description or "").strip() or DEFAULT_DESCRIPTION
    steps: list[str] = []
    try:
        rounds = max(0, int(max_repair_rounds or 0))
    except (TypeError, ValueError):
        rounds = 1
    try:
        return _compile(text, llm=llm, knobs=normalize_knobs(knobs), max_repair_rounds=rounds, steps=steps)
    except Exception as exc:  # noqa: BLE001 - 最外层兜底，保证编译永不崩
        steps.append(f"降级：编译流程异常（{type(exc).__name__}: {exc}），返回描述兜底卡")
        card = fallback_card(text, knobs, reason="编译流程异常")
        return CompileResult(
            card=card,
            critique=heuristic_critique(card),
            repair_rounds=0,
            health=fallback_health_report(card, "编译流程异常"),
            steps=steps,
            used_llm=False,
        )


__all__ = [
    "DEFAULT_DESCRIPTION",
    "compile_persona",
    "fallback_card",
    "heuristic_critique",
    "normalize_knobs",
]
