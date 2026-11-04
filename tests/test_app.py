"""app.py 的端到端测试：用 streamlit 官方 AppTest 真跑一遍界面脚本（离线演示模式，不联网）。

覆盖：首次启动无异常 → 连聊 4 轮触发记忆固化 → 切到记忆舱/实验室视图能看到五层记忆 →
原文库检索能回溯到具体那句话 → 记忆可纠正 → 清空当前角色记忆后回到空状态。
"""

from __future__ import annotations

import os

import pytest
from streamlit.testing.v1 import AppTest

from core import config as cfg

TURNS = (
    "你好呀，我叫小林，下周三要去面试。",
    "我最讨厌别人骗我了。",
    "我妈最近住院了，我有点撑不住。",
    "我还喜欢下雨天，安静。",
)
VIEW_MEMORY = "🧠 记忆舱"
VIEW_LAB = "🧪 实验室"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """把记忆文件、角色文件、快照目录都指到临时目录，避免污染真实 data/。"""
    import streamlit as st

    monkeypatch.setenv("EMO_DEMO_MODE", "1")
    monkeypatch.setattr(cfg, "MEMORY_FILE", tmp_path / "memory.json", raising=False)
    monkeypatch.setattr(cfg, "ROLE_FILE", tmp_path / "roles.json", raising=False)
    monkeypatch.setattr(cfg, "CUSTOM_ROLES_FILE", tmp_path / "roles.json", raising=False)
    monkeypatch.setattr(cfg, "ARCHIVE_DIR", tmp_path / "archive", raising=False)
    st.cache_resource.clear()

    instance = AppTest.from_file("app.py", default_timeout=120)
    instance.run()
    yield instance
    st.cache_resource.clear()


def _say(instance: AppTest, text: str) -> AppTest:
    instance.chat_input[0].set_value(text).run()
    assert not instance.exception, instance.exception
    return instance


def _goto(instance: AppTest, view: str) -> AppTest:
    """切到指定视图（对话 / 记忆舱 / 实验室）。"""
    instance.radio[0].set_value(view).run()
    assert not instance.exception, instance.exception
    return instance


def _markdown(instance: AppTest) -> str:
    return "\n".join(markdown.value for markdown in instance.markdown)


def test_boot_renders_shell(app):
    assert not app.exception
    assert app.title, "主标题应该渲染出来"
    assert any("EmoMem" in title.value for title in app.title)
    assert app.chat_input, "对话页应该有输入框"
    sidebar_text = " ".join(item.value for item in [*app.sidebar.markdown, *app.sidebar.warning, *app.sidebar.caption])
    assert "演示" in sidebar_text or "API" in sidebar_text
    assert app.session_state["role"]


def test_views_switch_and_memory_cabin_has_five_layers(app):
    _goto(app, VIEW_MEMORY)
    assert len(app.tabs) >= 7, "记忆舱应该有五层 + 关系档案等 tab"
    labels = [tab.label for tab in app.tabs]
    for expected in ("五层总览", "L0 原文库", "L1 会话层", "L2 长期事实", "L3 固化快照", "L4 自我认知"):
        assert any(expected in label for label in labels), f"缺少 {expected} 面板"
    _goto(app, VIEW_LAB)
    assert len(app.tabs) >= 3, "实验室应该有 Trace / 消融 / 体检"


def test_four_turns_write_memory_and_panels(app):
    for text in TURNS:
        _say(app, text)

    last_turn = app.session_state["last_turn"]
    assert last_turn["reply"]
    assert last_turn["memory_block"]
    assert "reply" in last_turn["system_prompt"]

    _goto(app, VIEW_MEMORY)
    joined = _markdown(app)
    assert "L0 原文库" in joined
    assert "小林" in joined, "用户自报的名字应该出现在记忆面板里"
    assert "长期" in joined or "事实" in joined


def test_archive_search_finds_the_original_sentence(app):
    from ui import state

    for text in TURNS:
        _say(app, text)

    role = app.session_state["role"]
    hits = state.search_archive(role, "面试", limit=5)
    assert hits, "原文库应该能检索到说过的话"
    assert any("面试" in hit["text"] for hit in hits[:3])
    assert all(hit.get("ts_label") for hit in hits), "每条原文都要带时间戳"

    stats = state.archive_stats(role)
    assert stats["messages"] >= len(TURNS)
    assert stats["user_messages"] >= len(TURNS)


def test_chat_history_is_persisted_per_role(app):
    _say(app, "我最近加班到很晚，有点撑不住")
    threads = [block for block in app.get("chat_message")]
    assert len(threads) >= 2, "至少应该有开场白 + 一轮问答的气泡"
    assert "加班" in _markdown(app)


def test_correct_fact_supersedes_old_one(app):
    """记忆可纠正：用户改一条事实 → 新增高置信事实，旧事实进审计。"""
    from ui import state

    for text in TURNS:
        _say(app, text)
    role = app.session_state["role"]
    facts = state.build_engine().snapshot_for_ui(role)["long"]["items"]
    assert facts, "四轮对话后应该沉淀出事实"
    victim = facts[0]["id"]

    assert state.correct_fact(role, victim, "用户叫小林，住在南方") is True
    after = state.build_engine().snapshot_for_ui(role)["long"]["items"]
    corrected = [item for item in after if item["content"] == "用户叫小林，住在南方"]
    assert corrected, "纠正后应该出现新事实"
    assert corrected[0]["confidence"] >= cfg.CORRECTION_CONFIDENCE
    old = [item for item in after if item["id"] == victim]
    assert old and old[0]["superseded_by"] == corrected[0]["id"], "旧事实应被标记为已被取代"
    assert state.correct_fact(role, "fact_does_not_exist", "随便") is False


def test_reset_current_role_returns_to_empty(app):
    for text in TURNS[:2]:
        _say(app, text)

    reset_button = next(button for button in app.button if "清空当前角色记忆" in button.label)
    reset_button.click().run()
    assert not app.exception
    assert app.session_state["last_turn"] is None
    _goto(app, VIEW_MEMORY)
    assert "小林" not in _markdown(app), "清空后不应该还留着上一轮的记忆"


def test_forget_memory_deletes_single_item(app):
    """记忆主权：用户可以让 TA 忘掉某一条具体记忆，其余记忆不受影响。"""
    from ui import state

    for text in TURNS:
        _say(app, text)

    role = app.session_state["role"]
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
    _goto(app, VIEW_MEMORY)
    forget_button = next(
        button for button in app.button if button.label.strip().endswith("忘掉") and "这段" not in button.label
    )
    forget_button.click().run()
    assert not app.exception

def test_api_key_problem_flags_placeholder(monkeypatch):
    """手滑写 Key 的两种典型情形要在侧边栏直接报出来，而不是默默降级成离线演示模式。"""
    from core import llm
    from ui import state

    monkeypatch.delenv("EMO_DEMO_MODE", raising=False)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-xxxxsk-dummy000000000000000000000000000000")
    assert "占位符" in state.api_key_problem()
    assert llm.is_demo_mode() is True, "坏 Key 应当按未配置处理，直接降级到离线演示模式"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fde19a5059a14d7f841d1931bdca60e3")
    assert "sk-" in state.api_key_problem()
    assert llm.is_demo_mode() is True

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-dummy000000000000000000000000000000")
    assert state.api_key_problem() == ""
    assert llm.is_demo_mode() is False

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert state.api_key_problem() == ""
    assert llm.is_demo_mode() is True

def test_session_key_overrides_env_and_never_shows_plaintext(monkeypatch):
    """访客自己填的 Key：只走会话上下文，不写 os.environ，界面上只露末 4 位。"""
    from core import llm

    monkeypatch.delenv("EMO_DEMO_MODE", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-envenvenvenvenvenvenvenvenv0001")

    llm.set_session_api_key("sk-" + "a" * 32 + "9999")
    try:
        assert llm.key_source() == "session"
        assert llm.active_api_key().endswith("9999")
        assert llm.key_fingerprint() == "sk-…9999"
        assert "aaaa" not in llm.mode_label()
        assert llm.is_demo_mode() is False
        assert os.environ["DEEPSEEK_API_KEY"].endswith("0001"), "会话 Key 不应该覆盖进程环境变量"
    finally:
        llm.set_session_api_key("")

    assert llm.key_source() == "env"
    assert llm.key_fingerprint().endswith("0001")


def test_api_key_input_is_masked_and_never_echoed(app, monkeypatch):
    """侧边栏粘 Key 后：页面只显示指纹，任何组件里都不出现明文。"""
    secret = "sk-" + "b" * 32 + "4321"
    monkeypatch.delenv("EMO_DEMO_MODE", raising=False)
    app.run()

    from ui import state as ui_state

    app.text_input(key=ui_state.key_input_key()).set_value(secret).run()
    next(button for button in app.button if button.label.strip() == "应用 Key").click().run()
    assert not app.exception

    rendered = " ".join(item.value for item in app.markdown)
    rendered += " ".join(item.value for item in app.sidebar.caption)
    rendered += " ".join(item.value for item in app.sidebar.success)
    assert secret not in rendered, "明文 Key 绝不能出现在页面上"
    assert "4321" in rendered, "应该显示末 4 位指纹，方便确认连的是哪把 Key"
    assert os.getenv("DEEPSEEK_API_KEY", "") != secret
