"""persona_forge —— 人设编译器（Persona Compiler）。

把「一句话角色描述」编译成结构化人设卡，再经自评、定向重写、人格体检，最终渲染成系统提示词。
这是 EmoMem 的 7 号创新点：元 Prompt 不直接产出人设字符串，而是产出**可执行的人设指令**。

典型用法::

    from persona_forge import compile_persona, build_system_prompt

    result = compile_persona("一个退休刑警开的馄饨摊老板", knobs={"毒舌": 0.6})
    prompt = build_system_prompt(result.card, memory_block, stage="熟悉", nickname=None)
    result.critique.average      # 6 维综合分
    result.health.summary        # 3 问体检结论

模块划分：

- :mod:`persona_forge.schema` —— 数据模型与容错转换（PersonaCard / Critique / HealthReport / CompileResult）；
- :mod:`persona_forge.prompts` —— 四段元 Prompt 常量与调用适配层；
- :mod:`persona_forge.compiler` —— 编译流水线 ``compile_persona``；
- :mod:`persona_forge.healthcheck` —— 3 个人格体检问题；
- :mod:`persona_forge.render` —— 系统提示词与人设段渲染；
- :mod:`persona_forge.legacy` —— 旧版角色兼容包装。
"""

from __future__ import annotations

from persona_forge.compiler import (
    DEFAULT_DESCRIPTION,
    compile_persona,
    fallback_card,
    heuristic_critique,
    normalize_knobs,
)
from persona_forge.healthcheck import (
    extract_probe_reply,
    fallback_health_report,
    judge_reply,
    run_health_check,
    style_hit,
)
from persona_forge.legacy import from_legacy, legacy_opening
from persona_forge.prompts import (
    BANNED_CLICHES,
    CRITIQUE_RUBRIC,
    EXAMPLE_CARD_JSON,
    PERSONA_CARD_JSON_SCHEMA,
    PERSONA_CRITIQUE_SYSTEM,
    PERSONA_PROBE_SYSTEM,
    PERSONA_REPAIR_SYSTEM,
    PERSONA_SEED_SYSTEM,
    PERSONA_SEED_USER,
    render_knobs_text,
)
from persona_forge.render import (
    STAGE_PROFILES,
    build_system_prompt,
    render_memory_section,
    render_persona_section,
    render_protocol_rules,
    render_style_directives,
)
from persona_forge.schema import (
    CRITIQUE_DIMENSIONS,
    CompileResult,
    Critique,
    EmotionRule,
    HealthReport,
    PersonaCard,
    SpeechProfile,
    card_to_dict,
    critique_to_dict,
    dict_to_card,
    dict_to_critique,
    health_to_dict,
    validate_card,
)

__all__ = [
    "BANNED_CLICHES",
    "CRITIQUE_DIMENSIONS",
    "CRITIQUE_RUBRIC",
    "DEFAULT_DESCRIPTION",
    "CompileResult",
    "Critique",
    "EXAMPLE_CARD_JSON",
    "EmotionRule",
    "HealthReport",
    "PERSONA_CARD_JSON_SCHEMA",
    "PERSONA_CRITIQUE_SYSTEM",
    "PERSONA_PROBE_SYSTEM",
    "PERSONA_REPAIR_SYSTEM",
    "PERSONA_SEED_SYSTEM",
    "PERSONA_SEED_USER",
    "PersonaCard",
    "STAGE_PROFILES",
    "SpeechProfile",
    "build_system_prompt",
    "card_to_dict",
    "compile_persona",
    "critique_to_dict",
    "dict_to_card",
    "dict_to_critique",
    "extract_probe_reply",
    "fallback_card",
    "fallback_health_report",
    "from_legacy",
    "health_to_dict",
    "heuristic_critique",
    "judge_reply",
    "legacy_opening",
    "normalize_knobs",
    "render_knobs_text",
    "render_memory_section",
    "render_persona_section",
    "render_protocol_rules",
    "render_style_directives",
    "run_health_check",
    "style_hit",
    "validate_card",
]
