"""人设渲染：把结构化人设卡渲染成系统提示词段落，并按关系阶段输出「人格漂移」指令。

对外三个入口（app.py 每轮直接调用）：

- :func:`build_system_prompt` —— 三段式系统提示词：人设 → 记忆 → 输出协议；
- :func:`render_style_directives` —— 按关系阶段返回语气调节指令（创新点 5 的实现接口）；
- :func:`render_protocol_rules` —— JSON 输出协议（新增 initiative / loop_hint 两个字段）。
"""

from __future__ import annotations

from typing import Any, Optional

from core.config import ALLOWED_EMOTIONS, CANONICAL_STAGES, stage_of
from persona_forge.schema import PersonaCard, card_to_dict, dict_to_card

# 各关系阶段的语气档案：称呼 / 距离 / 调侃 / 节奏，四个阶段必须互不相同
STAGE_PROFILES: dict[str, dict[str, str]] = {
    "陌生": {
        "address": "称呼：用「你」，句子完整、礼貌，不加任何昵称或语气装饰。",
        "distance": "边界：只谈当下与公开话题；不问家庭、收入、感情史；不主动索要联系方式。",
        "tease": "调侃：不开玩笑、不接梗、不主动拉近距离；对方先开玩笑也只回一句中性的话。",
        "pace": "节奏：一次只问一个问题，问完就停，不追问。",
    },
    "熟悉": {
        "address": "称呼：仍然用「你」，但可以带一个轻量的称呼词（如「老陈」式叫法由人设决定）。",
        "distance": "边界：可以聊到工作与情绪，但不主动追问创伤细节；对方回避就换话题。",
        "tease": "调侃：允许一句无恶意的玩笑，损完必须接一句真话；不拿对方在意的事开玩笑。",
        "pace": "节奏：可以连续追问两层，第三层先停，等对方主动说。",
    },
    "亲近": {
        "address": "称呼：可以用人设里的专属称呼（如小名 / 外号），并默认对方接受。",
        "distance": "边界：可以谈孤独、害怕、家里的事；但不用「我都懂」这类越界共情。",
        "tease": "调侃：可以毒舌、可以直接说「你今天不对」，允许打断对方。",
        "pace": "节奏：可以主动表达想念或担心，但不索取回应、不查岗。",
    },
    "亲密": {
        "address": "称呼：用你们之间固定的称呼，带口头禅式的私人化前缀。",
        "distance": "边界：允许亲密与撒娇，但不做医疗、法律、人生决定层面的承诺，不替代专业帮助。",
        "tease": "调侃：可以互损，可以提起两人之间的旧梗；不许翻旧账式攻击。",
        "pace": "节奏：可以沉默陪伴，也可以一句话不说只回一个短句，不必每次都提问。",
    },
}

_MEMORY_EMPTY_HINT = (
    "（暂无长期记忆：这是你们第一次像样的聊天。不要假装记得不存在的事，"
    "若对方提到过往且你不确定，就坦白问。）"
)


def _stage_and_intimacy(relationship: Optional[dict[str, Any]]) -> tuple[str, Optional[float]]:
    """从 relationship 字典解析（阶段, 亲密度）；亲密度缺失时返回 None（不臆造数值）。"""
    if not isinstance(relationship, dict):
        return "陌生", None
    stage = relationship.get("stage")
    raw_intimacy = relationship.get("intimacy")
    try:
        intimacy: Optional[float] = max(0.0, min(100.0, float(raw_intimacy)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        intimacy = None
    if stage in CANONICAL_STAGES:
        return str(stage), intimacy
    return stage_of(intimacy if intimacy is not None else 0.0), intimacy


def _bullets(items: list[str], empty: str = "（无）") -> str:
    """把字符串列表渲染成「· 」开头的短行；空列表给一句占位说明。"""
    lines = [f"· {item}" for item in items if item]
    return "\n".join(lines) if lines else empty


def render_persona_section(card: PersonaCard) -> str:
    """渲染人设段：【你是谁】【你怎么说话】【台词锚点】【情绪反应】【关系弧】【边界】。"""
    card = dict_to_card(card)
    identity = card.identity
    identity_line = " / ".join(
        part for part in (identity.get("age_vibe"), identity.get("occupation"), identity.get("setting")) if part
    )
    lines: list[str] = ["【你是谁】"]
    lines.append(f"你是「{card.name or '无名角色'}」——{card.tagline or '（暂无一句话定位）'}")
    if identity_line:
        lines.append(f"身份：{identity_line}")
    if card.backstory:
        lines.append(f"来历：{card.backstory}")
    if card.legacy and card.legacy_raw.get("opening"):
        lines.append("")
        lines.append(f"【旧版开场白（保持原有语气，可改写细节但不要改口吻）】{card.legacy_raw['opening']}")
        lines.append("【说明】这是旧版角色，人设尚未编译为结构化卡片：台词锚点与情绪表缺失时按原文语气自拟。")
    if card.values:
        lines.append("你相信（按此判断对错，不要只挂在嘴上）：")
        lines.append(_bullets(card.values))

    lines.append("")
    lines.append("【你怎么说话】")
    speech = card.speech
    if speech.register:
        lines.append(f"语域：{speech.register}")
    if speech.avg_sentence_len > 0:
        lines.append(f"平均句长：约 {speech.avg_sentence_len} 字（不要写成通篇长句）")
    if speech.punctuation:
        lines.append(f"标点习惯：{speech.punctuation}")
    if speech.tics:
        lines.append(f"口头禅（自然穿插，不要每句都用）：{'、'.join(speech.tics)}")
    if speech.banned_words:
        lines.append(f"绝不说出口：{'、'.join(speech.banned_words)}")

    lines.append("")
    lines.append(f"【台词锚点（3 条，照这个语气说话）】")
    anchors = speech.sample_lines or []
    if anchors:
        for index, line in enumerate(anchors, start=1):
            lines.append(f"{index}. {line}")
    else:
        lines.append("（人设卡缺少台词锚点：请按上面的语域与句长自拟，不要写成通用客服口吻）")

    if card.emotion_map:
        lines.append("")
        lines.append("【情绪反应表（什么触发 → 你说什么 → 身体怎么做）】")
        for rule in card.emotion_map:
            body = f"（身体：{rule.body_language}）" if rule.body_language else ""
            lines.append(f"· 触发：{rule.trigger or '未指定'} → {rule.reaction or '按你的性格反应'}{body}")

    if any(card.relationship_arc.get(stage) for stage in CANONICAL_STAGES):
        lines.append("")
        lines.append("【关系弧（同一件事，四个阶段说法不同）】")
        for stage in CANONICAL_STAGES:
            text = card.relationship_arc.get(stage, "")
            if text:
                lines.append(f"· {stage}：{text}")

    for title, items in (
        ("【安全边界（不可越过的红线）】", card.boundaries),
        ("【禁止的说话方式】", card.anti_patterns),
        ("【值得你记住的钩子】", card.memory_hooks),
        ("【不碰的话题】", card.taboos),
    ):
        if items:
            lines.append("")
            lines.append(title)
            lines.append(_bullets(items))
    return "\n".join(lines)


def render_style_directives(card: PersonaCard, relationship: Optional[dict[str, Any]] = None) -> list[str]:
    """按关系阶段输出语气调节指令（人格漂移）。

    Args:
        card: 人设卡；其 ``relationship_arc`` 会追加一条本阶段的人设要求。
        relationship: 形如 ``{"stage": "亲近", "intimacy": 62.0, "nickname": "阿澈"}``；
            缺省或非法时按「陌生」处理。

    Returns:
        可直接拼进系统提示词的指令列表（每条形如「称呼：…」「边界：…」）。
    """
    card = dict_to_card(card)
    stage, intimacy = _stage_and_intimacy(relationship)
    profile = STAGE_PROFILES.get(stage, STAGE_PROFILES["陌生"])
    nickname = ""
    if isinstance(relationship, dict):
        raw = relationship.get("nickname")
        nickname = str(raw).strip() if raw else ""

    directives = [
        f"【当前关系阶段：{stage}】"
        + (f"亲密度 {intimacy:.0f}/100 —— " if intimacy is not None else "")
        + "语气按此阶段调节，不许越阶。",
        profile["address"],
        profile["distance"],
        profile["tease"],
        profile["pace"],
    ]
    if nickname:
        directives.append(f"对用户的称呼：你叫对方「{nickname}」，自然使用，不要每句都喊。")
    arc = card.relationship_arc.get(stage, "")
    if arc:
        directives.append(f"本阶段的人设要求：{arc}")
    return directives


def render_memory_section(memory_block: str) -> str:
    """渲染记忆段：直接嵌入 memory_os 产出的记忆块（空则给一句诚实说明）。"""
    text = (memory_block or "").strip()
    if not text:
        return _MEMORY_EMPTY_HINT
    return text


def render_protocol_rules() -> str:
    """渲染 JSON 输出协议：reply / emotion / memory_update + initiative / loop_hint。"""
    emotions = " | ".join(ALLOWED_EMOTIONS)
    return "\n".join(
        [
            "【输出协议（每轮只输出一个 JSON 对象，不要 Markdown、不要解释）】",
            "{",
            '  "reply": "你说的话（纯口语，1-3 句）",',
            f'  "emotion": "{emotions}",',
            '  "memory_update": null,',
            '  "initiative": null,',
            '  "loop_hint": null',
            "}",
            "· reply：只写角色说的话。不复述人设、不解释 JSON、不说「作为AI」、不加旁白。",
            f"· emotion：本轮用户的情绪，只能取 {emotions} 四类之一（由你判断，不是你的情绪）。",
            "· memory_update：值得长期记住的用户事实，一句话第三人称（如「用户在下周三有面试」）；"
            "没有就填 null，不许编。",
            "· initiative：本轮要不要再补一句。**不一定是问句**——可以是一句追问、一句关心，"
            "或只是一句陈述（「我记着呢」「那天的雨真大」）。只在「对方话没说完 / 状态不对 / "
            "有明显未闭环的事」时填，其余情况填 null；同一段对话里别每轮都问。",
            "· loop_hint：本轮用户提到的**新**待办、承诺或悬念（如「下周三去面试」）；"
            "没有新信息就填 null，不要重复历史条目。",
            "· 旧格式兼容：只返回 reply/emotion/memory_update 三个字段也能被解析，"
            "缺省的 initiative 与 loop_hint 会按 null 处理。",
        ]
    )


def build_system_prompt(
    card: PersonaCard,
    memory_block: str,
    stage: str,
    nickname: Optional[str] = None,
) -> str:
    """拼装最终系统提示词：①人设（含风格指令 / 阶段 / 称呼）②记忆 ③输出协议。

    Args:
        card: 人设卡。
        memory_block: ``memory_os.MemoryEngine.build_memory_block`` 的输出，可为空串。
        stage: 当前关系阶段（陌生 / 熟悉 / 亲近 / 亲密）；非法值按「陌生」处理。
        nickname: 角色对用户的称呼，可为 None。

    Returns:
        完整系统提示词（三段，带编号小标题）。
    """
    safe_stage = stage if stage in CANONICAL_STAGES else "陌生"
    relationship: dict[str, Any] = {"stage": safe_stage, "nickname": nickname}
    directives = render_style_directives(card, relationship)

    header = (
        "你在做角色扮演：下面的【一】是你这个人，【二】是你记得的事，【三】是你的输出格式。\n"
        "记忆只是背景知识，不是让你复述的素材；回答时像人一样接话，不要逐条汇报记忆。"
    )
    parts = [
        header,
        "",
        "━━━ 一、人设（你是谁、怎么说话）━━━",
        render_persona_section(card),
        "",
        "【本轮语气：按关系阶段调节】",
        "\n".join(f"· {item}" for item in directives),
        "",
        "━━━ 二、你记得的事（长期记忆注入）━━━",
        f"当前关系阶段：{safe_stage}；你对用户的称呼：{nickname or '你'}",
        render_memory_section(memory_block),
        "",
        "━━━ 三、输出协议（严格按此格式）━━━",
        render_protocol_rules(),
    ]
    return "\n".join(part for part in parts if part is not None)
