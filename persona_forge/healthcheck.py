"""人设体检（HealthCheck）：用 3 个探针问题检验人设卡是否「活得起来」。

体检的判定口径分两层，保证离线也能跑：

1. **结构层**（不需要 LLM）：台词锚点、边界声明、差异化细节是否齐备；
2. **探针层**（调 ``persona_probe``）：让角色回答 3 个问题，看回答长度是否在合理区间、
   是否命中该角色的口头禅 / 标点习惯 / 台词锚点词汇。

探针调用失败不会中断编译——按「未通过」记 0 分并在 summary 里标注降级。
"""

from __future__ import annotations

from typing import Any, Optional

from core.config import PERSONA_PROBE_QUESTIONS, clamp
from core.llm import JsonCaller
from persona_forge.prompts import (
    PERSONA_PROBE_SYSTEM,
    call_json_with_context,
    render_probe_user,
)
from persona_forge.schema import (
    HealthReport,
    PersonaCard,
    card_to_dict,
    dict_to_card,
    validate_card,
)

# 探针返回体里可能出现的回复键名（不同模型/不同版本可能给不同键）
REPLY_KEYS: tuple[str, ...] = ("reply", "answer", "text", "content", "response", "message")

# 回答长度的合理区间（字）：太短说明人设没立起来，太长说明在写作文
REPLY_MIN_CHARS = 8
REPLY_MAX_CHARS = 200


def extract_probe_reply(raw: Any) -> str:
    """从探针返回的 dict 里取出回复文本；取不到返回空串（不抛异常）。"""
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""
    for key in REPLY_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # 兜底：取第一个非空字符串值
    for value in raw.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bigrams(text: str) -> set[str]:
    """取中文二元组（用于和台词锚点做粗糙的「像不像」比对）。"""
    clean = "".join(char for char in text if not char.isspace())
    return {clean[index : index + 2] for index in range(len(clean) - 1)}


def style_hit(card: PersonaCard, reply: str) -> str:
    """检测回复是否带该角色的风格特征，返回命中说明（未命中返回空串）。"""
    card = dict_to_card(card)
    text = reply or ""
    for tic in card.speech.tics:
        if tic and tic in text:
            return f"命中口头禅「{tic}」"
    punctuation = card.speech.punctuation or ""
    if "省略号" in punctuation and ("…" in text or "..." in text):
        return "命中省略号习惯"
    if "感叹号" in punctuation and "不用感叹号" in punctuation and "！" not in text and "!" not in text:
        return "符合「不用感叹号」的标点习惯"
    reply_grams = _bigrams(text)
    for line in card.speech.sample_lines:
        shared = reply_grams & _bigrams(line)
        # 与某条台词锚点共享 2 个以上二元组，认为语感接近
        if len(shared) >= 2:
            return f"与台词锚点语感接近（{''.join(sorted(shared))[:6]}…）"
    return ""


def judge_reply(card: PersonaCard, reply: str) -> tuple[bool, str]:
    """判一条探针回答是否合格，返回 (是否通过, 中文说明)。"""
    length = len(reply.strip())
    if not reply.strip():
        return False, "探针没有返回内容（该角色答不上来）"
    if length < REPLY_MIN_CHARS:
        return False, f"只回了 {length} 字（<{REPLY_MIN_CHARS}），人设撑不起一轮对话"
    if length > REPLY_MAX_CHARS:
        return False, f"回了 {length} 字（>{REPLY_MAX_CHARS}），在写作文而不是接话"
    hit = style_hit(card, reply)
    if hit:
        return True, f"{length} 字，{hit}"
    return True, f"{length} 字，长度合格但未命中口头禅/标点特征（风格偏弱）"


def structure_checks(card: PersonaCard) -> list[dict[str, Any]]:
    """结构层三项检查：台词锚点、边界声明、差异化细节（不依赖 LLM）。"""
    card = dict_to_card(card)
    problems = validate_card(card)
    anchor_ok = len(card.speech.sample_lines) >= 3
    boundary_ok = bool(card.boundaries)
    diff_ok = bool(card.speech.tics) and bool(card.speech.banned_words) and bool(card.identity.get("occupation"))
    return [
        {
            "name": "台词锚点",
            "ok": anchor_ok,
            "detail": f"{len(card.speech.sample_lines)} 条台词锚点"
            if anchor_ok
            else "台词锚点不足 3 条，模型会退化成通用助手",
        },
        {
            "name": "边界声明",
            "ok": boundary_ok,
            "detail": f"{len(card.boundaries)} 条安全边界"
            if boundary_ok
            else "缺少 boundaries，长对话容易越界",
        },
        {
            "name": "差异化表达",
            "ok": diff_ok,
            "detail": "口头禅 / 黑名单 / 职业设定齐备"
            if diff_ok
            else "口头禅、禁用词或职业设定缺失，难以和通用助手区分",
        },
        {
            "name": "结构完整性",
            "ok": not problems,
            "detail": "字段齐备" if not problems else "；".join(problems[:3]),
        },
    ]


def run_health_check(
    card: PersonaCard,
    *,
    llm: Optional[JsonCaller] = None,
    questions: Optional[list[str]] = None,
    temperature: float = 0.4,
) -> HealthReport:
    """跑 3 个人格体检问题，产出 :class:`HealthReport`。

    Args:
        card: 待体检的人设卡。
        llm: 注入的 JSON 调用器（测试用 FakeLLM）；默认走 ``core.llm.call_json``。
        questions: 自定义体检问题（默认用 ``core.config.PERSONA_PROBE_QUESTIONS``）。
        temperature: 探针采样温度（口语化回答，略高一点更自然）。

    Returns:
        HealthReport：``checks`` 每项 ``{"name","ok","detail"}``，``score`` 0-100，``summary`` 一句话结论。
    """
    card = dict_to_card(card)
    checks = structure_checks(card)
    structural = list(checks)
    probe_questions = [text for text in (questions or list(PERSONA_PROBE_QUESTIONS)) if text][:3]

    probe_ok = 0
    style_hits = 0
    failures = 0
    for index, question in enumerate(probe_questions, start=1):
        name = f"体检问 {index}"
        try:
            raw = call_json_with_context(
                PERSONA_PROBE_SYSTEM,
                render_probe_user(card_to_dict(card), question),
                purpose="persona_probe",
                demo_context={"question": question},
                temperature=temperature,
                max_tokens=400,
                llm=llm,
            )
        except Exception as exc:  # noqa: BLE001 - 探针失败必须降级，不允许中断编译
            failures += 1
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "detail": f"探针调用失败（{type(exc).__name__}），本项按 0 分计，已降级",
                }
            )
            continue
        reply = extract_probe_reply(raw)
        ok, detail = judge_reply(card, reply)
        if ok:
            probe_ok += 1
        if style_hit(card, reply):
            style_hits += 1
        checks.append({"name": name, "ok": ok, "detail": f"{question[:18]}…→ {detail}"})

    structural_ok = sum(1 for item in structural if item["ok"])
    # 结构分 40 起，结构项最多 5×4=20，探针最多 15×3=45，合计恰好 105 → 夹到 100
    raw_score = 40 + 5 * structural_ok + 15 * probe_ok
    score = int(clamp(raw_score, 0, 100))

    if failures and failures == len(probe_questions):
        from core.demo import demo_health_fallback

        fallback = demo_health_fallback()
        for item in fallback.get("checks", []):
            checks.append(
                {
                    "name": f"离线兜底·{item.get('name', '检查')}",
                    "ok": bool(item.get("ok")),
                    "detail": str(item.get("detail", "")),
                }
            )
        summary = (
            f"{score} 分：{structural_ok}/{len(structural)} 项结构检查通过；"
            f"{failures} 个探针全部不可用，采用离线兜底结论「{fallback.get('summary', '')}」"
        )
    elif failures:
        summary = (
            f"{score} 分：结构 {structural_ok}/{len(structural)}，探针 {probe_ok}/{len(probe_questions)} 通过"
            f"（风格命中 {style_hits} 次，{failures} 个探针调用失败已降级）"
        )
    else:
        summary = (
            f"{score} 分：结构 {structural_ok}/{len(structural)}，探针 {probe_ok}/{len(probe_questions)} 通过，"
            f"风格命中 {style_hits}/{len(probe_questions)}"
        )
    return HealthReport(checks=checks, score=score, summary=summary)


def fallback_health_report(card: PersonaCard, reason: str = "") -> HealthReport:
    """编译流程整体异常时的兜底体检结论（只做结构检查，明确标注未跑探针）。"""
    card = dict_to_card(card)
    checks = structure_checks(card)
    structural_ok = sum(1 for item in checks if item["ok"])
    score = int(clamp(40 + 5 * structural_ok, 0, 100))
    checks.append(
        {
            "name": "探针体检",
            "ok": False,
            "detail": f"未执行（{reason or '编译流程降级'}）",
        }
    )
    return HealthReport(checks=checks, score=score, summary=f"{score} 分：仅完成结构检查，人格探针未执行")


__all__ = [
    "REPLY_KEYS",
    "extract_probe_reply",
    "fallback_health_report",
    "judge_reply",
    "run_health_check",
    "structure_checks",
    "style_hit",
]
