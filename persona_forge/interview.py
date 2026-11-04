"""人格教练：用几轮对话把「想要的角色」问清楚，再交给编译器。

为什么要有它：一句话描述对大多数人太难（结果都是"温柔体贴的姐姐"），
而"被一步步追问"是人类最擅长的人设生产方式 —— 你随口答，教练负责收束。

设计要点：
- **不是问卷**：每一问的措辞由模型结合前面的回答现场组织（``persona_interview``），
  问崩了就回落到写死的五个基础问题，所以离线演示也能走完流程；
- **收束成交接稿**：答完五问后由模型写一段 80–150 字的「人设速写」（``persona_brief``），
  这段速写就是 :func:`persona_forge.compile_persona` 的输入 —— 聊出来的人设和
  一句话写出来的人设，走的是同一条「编译 → 自评 → 定向重写 → 体检」流水线；
- **永不抛异常**：任何一步失败都退化成模板文本，UI 不会因为模型抽风而白屏。
"""

from __future__ import annotations

from typing import Any, Optional

from core.llm import JsonCaller
from persona_forge.prompts import call_json_with_context

# 五问的顺序与用途（key 同时用于 briefing 模板）
STAGES: tuple[dict[str, str], ...] = (
    {
        "key": "relation",
        "label": "关系",
        "question": "先定关系：TA 对你来说是谁？",
        "hint": "邻居家的姐姐、退伍的老兵、图书馆管理员、游戏里认识的网友，或者一个你说不清的存在。",
        "placeholder": "比如：住对门、总在深夜还亮着灯的姐姐",
    },
    {
        "key": "speech",
        "label": "说话方式",
        "question": "TA 说话是什么节奏？",
        "hint": "句子长短、口癖、爱不爱用问句、会不会先问「吃了吗」再谈正事。",
        "placeholder": "比如：话很少，喜欢先沉默两秒，开口就一句「嗯，然后呢」",
    },
    {
        "key": "emotion",
        "label": "难受时的反应",
        "question": "你难受的时候，TA 会怎么做？",
        "hint": "不追问？先递杯水？讲个很冷的笑话把话题岔开？还是干脆陪你坐着不说话？",
        "placeholder": "比如：不劝我，也不追问，就说「先吃饭」然后自己去厨房",
    },
    {
        "key": "boundary",
        "label": "禁语与边界",
        "question": "有哪些话，你绝对不想听 TA 说？",
        "hint": "正能量口号、评价你的选择、「这有什么大不了的」、拿你和别人比……",
        "placeholder": "比如：别说「你已经很努力了」，别劝我大度",
    },
    {
        "key": "spark",
        "label": "只属于 TA 的细节",
        "question": "最后一个：有没有什么细节，是只属于 TA 的？",
        "hint": "小到可以很具体：总是在凌晨出现、口袋里永远有糖、说错话会摸鼻子、记得你所有旧事。",
        "placeholder": "比如：每次要说重话之前会先清一下嗓子",
    },
)

STAGE_KEYS: tuple[str, ...] = tuple(stage["key"] for stage in STAGES)

COACH_NAME = "人格教练"
COACH_AVATAR = "🧭"

_INTERVIEW_SYSTEM = """你是一位角色设计师，正在帮一位普通用户一步步想出他真正想要的那个 AI 角色。

你要做的不是填表，而是像朋友聊天那样追问：用户答一句，你就顺着这句再往下挖一层，
把笼统的词换成具体的画面。一次只问一个问题。

硬性要求：
1. 只输出一个 JSON 对象，字段为 question（一句中文问句，40 字以内）。
2. 问句必须承接用户上一句里出现过的具体词，不能换话题，不能一次问两件事。
3. 禁止问「性格怎么样」「有什么爱好」这类空泛问题；要问可观察的行为、具体的场景。
4. 不要复述用户的话，不要给建议，不要写解释。
"""

_BRIEF_SYSTEM = """你在把一段"角色创作访谈"整理成人设速写，供后续的人设编译器使用。

只输出一个 JSON 对象，字段：
- brief：80-150 字的中文散文，写清「TA 是谁 / 说话方式 / 我难受时 TA 怎么做 / 禁语 / 独有细节」，
  用第二人称写（"你"指这个角色），不要小标题、不要列表、不要形容词堆砌，尽量留用用户的原话。
- name_hint：一个符合气质的中文名字（2-3 字），不要"小明""小红"这类通用名。
- knobs：四个 0-1 的小数，键固定为「毒舌」「温柔」「话痨」「神秘」，按访谈内容给值。
"""


def new_state() -> dict[str, Any]:
    """一份全新的访谈进度（放进 ``st.session_state`` 用）。"""
    return {
        "turns": [],  # [{"role": "assistant"|"user", "content": str}]
        "answers": {},  # stage key -> 用户原话
        "index": 0,  # 下一问的下标
        "question": "",  # 当前待回答的问题
        "brief": "",
        "name_hint": "",
        "knobs": {},
        "done": False,
    }


def stage_at(index: int) -> Optional[dict[str, str]]:
    """取第 index 个阶段（越界返回 None）。"""
    if 0 <= index < len(STAGES):
        return STAGES[index]
    return None


def progress_text(state: dict[str, Any]) -> str:
    """``第 2 / 5 问`` 之类的进度文案。"""
    index = int(state.get("index") or 0)
    done = min(index, len(STAGES))
    return f"第 {min(done + 1, len(STAGES))} / {len(STAGES)} 问"


def _answers_block(answers: dict[str, str]) -> str:
    """把已答内容拼成给模型看的问答记录。"""
    lines: list[str] = []
    for stage in STAGES:
        value = (answers.get(stage["key"]) or "").strip()
        if value:
            lines.append(f"- {stage['label']}：{value}")
    return "\n".join(lines) or "（还没有回答）"


def ask_question(state: dict[str, Any], *, llm: Optional[JsonCaller] = None) -> str:
    """生成下一问（LLM 措辞，失败回落写死的问题），并把教练这句话记进 turns。"""
    stage = stage_at(int(state.get("index") or 0))
    if stage is None:
        return ""
    fallback = stage["question"]
    answers = state.get("answers") or {}
    user_prompt = (
        f"用户已经回答的内容：\n{_answers_block(answers)}\n\n"
        f"现在要挖的方向：{stage['label']}\n"
        f"参考问法（可改写得更贴合上一句）：{stage['question']}\n"
        f"可以提示的细节：{stage['hint']}"
    )
    question = fallback
    try:
        payload = call_json_with_context(
            _INTERVIEW_SYSTEM,
            user_prompt,
            purpose="persona_interview",
            demo_context={"stage": stage, "answers": answers, "index": state.get("index")},
            temperature=0.7,
            max_tokens=200,
            llm=llm,
        )
        candidate = str(payload.get("question") or "").strip()
        if 4 <= len(candidate) <= 80:
            question = candidate
    except Exception:  # 教练问崩了不该挡住用户
        question = fallback

    state["question"] = question
    state["turns"] = list(state.get("turns") or []) + [{"role": "assistant", "content": question}]
    return question


def take_answer(state: dict[str, Any], text: str) -> None:
    """记下用户这一问的回答，并推进到下一问。"""
    answer = (text or "").strip()
    if not answer:
        return
    stage = stage_at(int(state.get("index") or 0))
    if stage is not None:
        answers = dict(state.get("answers") or {})
        answers[stage["key"]] = answer
        state["answers"] = answers
    state["turns"] = list(state.get("turns") or []) + [{"role": "user", "content": answer}]
    state["index"] = int(state.get("index") or 0) + 1
    state["done"] = state["index"] >= len(STAGES)
    state["question"] = ""


def skip_stage(state: dict[str, Any]) -> None:
    """跳过当前这一问（记为"没想好"），继续往下走。"""
    stage = stage_at(int(state.get("index") or 0))
    if stage is not None:
        answers = dict(state.get("answers") or {})
        answers.setdefault(stage["key"], "")
        state["answers"] = answers
    state["index"] = int(state.get("index") or 0) + 1
    state["done"] = state["index"] >= len(STAGES)
    state["question"] = ""


def fallback_brief(answers: dict[str, str]) -> str:
    """不靠模型也能读的兜底速写（保留用户原话）。"""
    get = lambda key: (answers.get(key) or "").strip()  # noqa: E731
    parts = [f"你是{get('relation')}。" if get("relation") else "你是用户身边一个熟悉的角色。"]
    if get("speech"):
        parts.append(f"你说话是这样的：{get('speech')}。")
    if get("emotion"):
        parts.append(f"对方难受的时候，你会{get('emotion')}。")
    if get("boundary"):
        parts.append(f"有些话你不会说，比如{get('boundary')}。")
    if get("spark"):
        parts.append(f"你身上有个只属于你的细节：{get('spark')}。")
    return "".join(parts)


def compose(state: dict[str, Any], *, llm: Optional[JsonCaller] = None) -> dict[str, Any]:
    """把访谈收束成人设速写（编译器输入）。失败则用模板，绝不抛异常。"""
    answers = dict(state.get("answers") or {})
    fallback = fallback_brief(answers)
    name_hint = ""
    knobs: dict[str, float] = {}
    brief = fallback
    try:
        payload = call_json_with_context(
            _BRIEF_SYSTEM,
            f"访谈记录：\n{_answers_block(answers)}\n\n请整理成人设速写。",
            purpose="persona_brief",
            demo_context={"answers": answers},
            temperature=0.7,
            max_tokens=800,
            llm=llm,
        )
        candidate = str(payload.get("brief") or "").strip()
        if len(candidate) >= 20:
            brief = candidate
        name_hint = str(payload.get("name_hint") or "").strip()[:8]
        raw_knobs = payload.get("knobs") or {}
        if isinstance(raw_knobs, dict):
            for key, value in raw_knobs.items():
                try:
                    knobs[str(key)] = max(0.0, min(1.0, float(value)))
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass

    state["brief"] = brief
    state["name_hint"] = name_hint
    state["knobs"] = knobs
    return {"brief": brief, "name_hint": name_hint, "knobs": knobs}
