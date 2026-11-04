"""人格教练（对话式创建角色）的离线测试：五问、接话、降级、收束、编译。"""

from __future__ import annotations

import pytest

from persona_forge import compile_persona, interview

ANSWERS = (
    "住对门、总在深夜还亮着灯的姐姐",
    "话很少，开口先停两秒，常说「嗯，然后呢」",
    "不劝我，也不追问，就说先吃饭",
    "别说你已经很努力了，也别劝我大度",
    "说重话之前会先清一下嗓子",
)


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    """教练的每句话都走离线演示模式，测试不联网。"""
    monkeypatch.setenv("EMO_DEMO_MODE", "1")


def _drive(answers=ANSWERS, llm=None) -> dict:
    state = interview.new_state()
    for text in answers:
        interview.ask_question(state, llm=llm)
        interview.take_answer(state, text)
    return state


def test_five_stages_make_a_full_transcript():
    state = _drive()
    assert state["done"] is True
    assert state["index"] == len(interview.STAGES) == 5
    assert len(state["answers"]) == 5
    roles = [turn["role"] for turn in state["turns"]]
    assert roles[:2] == ["assistant", "user"]
    assert len(roles) == 10  # 五问五答


def test_demo_question_reacts_to_previous_answer():
    state = interview.new_state()
    first = interview.ask_question(state)
    interview.take_answer(state, ANSWERS[0])
    second = interview.ask_question(state)
    assert first and second
    assert first != second
    assert "我记下了" in second  # 演示模式会把上一句拎出来接话


def test_llm_failure_falls_back_to_scripted_questions():
    def boom(*args, **kwargs):
        raise RuntimeError("model is down")

    state = interview.new_state()
    assert interview.ask_question(state, llm=boom) == interview.STAGES[0]["question"]
    interview.take_answer(state, ANSWERS[0])
    assert interview.ask_question(state, llm=boom) == interview.STAGES[1]["question"]


def test_skip_stage_advances_without_recording_an_answer():
    state = interview.new_state()
    interview.ask_question(state)
    interview.skip_stage(state)
    assert state["index"] == 1
    assert state["done"] is False
    assert state["question"] == ""


def test_compose_brief_keeps_user_wording_and_compiles():
    state = _drive()
    composed = interview.compose(state)
    brief = composed["brief"]
    assert len(brief) >= 20
    assert "姐姐" in brief or "深夜" in brief  # 用户原话被保留下来
    assert composed["name_hint"]

    result = compile_persona(brief, knobs=composed["knobs"] or None)
    assert result.card.name.strip()
    assert result.card.speech.sample_lines
    assert result.health.checks


def test_compose_survives_llm_failure():
    state = _drive()

    def boom(*args, **kwargs):
        raise RuntimeError("down")

    composed = interview.compose(state, llm=boom)
    assert composed["brief"] == interview.fallback_brief(state["answers"])
    assert "姐姐" in composed["brief"]
