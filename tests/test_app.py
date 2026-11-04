"""app.py 的端到端测试：用 streamlit 官方 AppTest 真跑一遍界面脚本（离线演示模式，不联网）。

覆盖：首次启动无异常 → 连聊 4 轮触发记忆固化 → 右栏三层/Trace/关系面板有内容 →
清空当前角色记忆后回到空状态。
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from core import config as cfg

TURNS = (
    "你好呀，我叫小林，下周三要去面试。",
    "我最讨厌别人骗我了。",
    "我妈最近住院了，我有点撑不住。",
    "我还喜欢下雨天，安静。",
)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """把记忆文件与角色文件指到临时目录，避免污染真实 data/。"""
    import streamlit as st

    monkeypatch.setenv("EMO_DEMO_MODE", "1")
    monkeypatch.setattr(cfg, "MEMORY_FILE", tmp_path / "memory.json", raising=False)
    monkeypatch.setattr(cfg, "ROLE_FILE", tmp_path / "roles.json", raising=False)
    monkeypatch.setattr(cfg, "CUSTOM_ROLES_FILE", tmp_path / "roles.json", raising=False)
    st.cache_resource.clear()

    instance = AppTest.from_file("app.py", default_timeout=120)
    instance.run()
    yield instance
    st.cache_resource.clear()


def _say(instance: AppTest, text: str) -> AppTest:
    instance.chat_input[0].set_value(text).run()
    assert not instance.exception, instance.exception
    return instance


def test_boot_renders_shell(app):
    assert not app.exception
    assert app.title, "主标题应该渲染出来"
    assert any("EmoMem" in title.value for title in app.title)
    assert len(app.tabs) >= 4, "记忆舱应该有四个 tab"
    sidebar_text = " ".join(item.value for item in [*app.sidebar.markdown, *app.sidebar.warning, *app.sidebar.caption])
    assert "演示" in sidebar_text or "API" in sidebar_text
    assert app.session_state["role"]


def test_four_turns_write_memory_and_panels(app):
    for text in TURNS:
        _say(app, text)

    last_turn = app.session_state["last_turn"]
    assert last_turn["reply"]
    assert last_turn["memory_block"]
    assert "reply" in last_turn["system_prompt"]

    joined = "\n".join(markdown.value for markdown in app.markdown)
    assert "记忆舱" in joined
    assert "短期" in joined
    assert "小林" in joined, "用户自报的名字应该出现在记忆面板里"
    assert "长期" in joined or "事实" in joined


def test_chat_history_is_persisted_per_role(app):
    _say(app, "我最近加班到很晚，有点撑不住")
    threads = [block for block in app.get("chat_message")]
    assert len(threads) >= 2, "至少应该有开场白 + 一轮问答的气泡"
    joined = "\n".join(markdown.value for markdown in app.markdown)
    assert "加班" in joined


def test_reset_current_role_returns_to_empty(app):
    for text in TURNS[:2]:
        _say(app, text)

    reset_button = next(button for button in app.button if "清空当前角色记忆" in button.label)
    reset_button.click().run()
    assert not app.exception
    assert app.session_state["last_turn"] is None
    joined = "\n".join(markdown.value for markdown in app.markdown)
    assert "小林" not in joined, "清空后不应该还留着上一轮的记忆"


def test_forget_memory_deletes_single_item(app):
    """记忆主权：用户可以让 TA 忘掉某一条具体记忆，其余记忆不受影响。"""
    from ui import state

    for text in TURNS:
        _say(app, text)

    role = app.session_state["role"]
    assert any("忘掉" in button.label for button in app.button), "长期/中期面板应该提供逐条删除"

    engine = state.build_engine()
    facts = engine.snapshot_for_ui(role)["long"]["items"]
    assert facts, "四轮对话后应该沉淀出事实"
    victim = facts[0]["id"]
    survivors = {item["id"] for item in facts[1:]}

    assert state.forget_memory(role, victim, tier="long") is True
    remaining = {item["id"] for item in state.build_engine().snapshot_for_ui(role)["long"]["items"]}
    assert victim not in remaining
    assert survivors <= remaining, "删一条不应该连带删掉别的记忆"
    assert state.forget_memory(role, "fact_does_not_exist", tier="long") is False

    # 再走一次真实按钮点击，确认界面链路（按钮 → 删除 → rerun）没有异常
    forget_button = next(
        button for button in app.button if button.label.strip().endswith("忘掉") and "这段" not in button.label
    )
    forget_button.click().run()
    assert not app.exception
