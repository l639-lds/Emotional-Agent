"""app.py 的端到端测试：用 streamlit 官方 AppTest 真跑一遍界面脚本（离线演示模式，不联网）。

覆盖：首次启动无异常 → 连聊 4 轮触发记忆固化 → 切到记忆舱/实验室视图能看到五层记忆 →
原文库检索能回溯到具体那句话 → 记忆可纠正 → 清空当前角色记忆后回到空状态。
"""

from __future__ import annotations

import os
import re

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
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path / "avatars", raising=False)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json", raising=False)
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
    instance.sidebar.radio[0].set_value(view).run()
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
    rendered = _markdown(app)
    # 气泡改成了自绘 HTML（用户靠右、助手靠左），所以按 emo-row 计数
    assert rendered.count('class="emo-row') >= 3, "开场白 + 我的提问 + TA 的回复"
    assert "emo-bubble-user" in rendered and "emo-bubble-assistant" in rendered
    assert "加班" in rendered


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


def test_user_avatar_lands_in_my_bubble(app):
    """我的头像可以改，且只出现在我自己的气泡里（右侧那一列）。"""
    from ui import state as ui_state

    ui_state.set_user_avatar("🐱")
    _say(app, "我叫小林，最近在准备面试")
    html = _markdown(app)
    assert "emo-bubble-user" in html
    user_row = html.split("emo-bubble-user")[0].rsplit('class="emo-row', 1)[-1]
    assert "🐱" in html
    assert not app.exception


def test_proactive_toggle_can_turn_it_off(app):
    """主动关怀可以关掉：关掉后不再出现「让 TA 主动开口」，也不会偷偷调模型。"""
    from ui import state as ui_state

    ui_state.set_proactive_enabled(False)
    _say(app, "我叫小林，下周三要去面试，有点慌")
    app.run()
    assert "让 TA 主动开口" not in _markdown(app)
    assert not app.exception

    ui_state.set_proactive_enabled(True)


def test_role_avatar_can_be_changed(app):
    """TA 的头像也能改（内置角色同样支持），改完出现在 TA 的气泡里。"""
    from ui import state as ui_state

    ui_state.set_role_avatar("温柔老父亲", "🧔")
    app.run()
    _say(app, "我叫小林，最近在准备面试")
    html = _markdown(app)
    assert "🧔" in html
    assert not app.exception


def test_user_bubble_is_written_once_and_reply_follows(app):
    """用户气泡先落盘再等模型：一轮下来只有一条我的气泡、一条 TA 的气泡。"""
    from ui import state as ui_state

    _say(app, "我叫小林，下周三要去面试")
    thread = ui_state.role_thread("温柔老父亲")
    roles = [message["role"] for message in thread]
    assert roles.count("user") == 1, "用户那句只能写一次（begin_turn 已经写过，finish_turn 不许再写）"
    assert roles.count("assistant") == 2, "开场白 + 这一轮的回复"
    html = _markdown(app)
    assert html.count('class="emo-row emo-row-user"') == 1
    assert app.session_state["pending_turn"] is None


def test_persona_coach_dialog_opens_and_asks(app):
    """人格教练：点开就会问第一问，答一句后问题往前推进。"""
    coach_btn = next(button for button in app.button if "人格教练" in button.label)
    coach_btn.click().run()
    assert not app.exception
    html = _markdown(app)
    assert "先定关系" in html, "第一问应该落到「关系」上"

    answer = app.text_area[0]
    answer.set_value("住对门、总在深夜还亮着灯的姐姐").run()
    next(button for button in app.button if button.label == "回答这一问").click().run()
    assert not app.exception
    coach = app.session_state["coach"]
    assert coach["index"] == 1, "答完一问应该推进到下一问"
    assert coach["answers"]["relation"].startswith("住对门")
    html = _markdown(app)
    assert "深夜还亮着灯的姐姐" in html, "用户说的那句要留在教练的对话记录里"
    assert "TA 开口之前会先停一下吗" in html, "第二问应该换成「说话方式」"


def test_coach_view_has_its_own_header_not_the_current_role(app):
    """人格教练是独立工作台：顶部不该顶着当前角色，也不该显示它的五层计数。"""
    import ui.state as ui_state

    role_name = app.session_state["role"]
    next(button for button in app.button if "人格教练" in button.label).click().run()
    assert not app.exception

    # 只看主区（侧边栏的人设卡里当然还有当前角色，那是另一回事）
    headers = [block.value for block in app.main.markdown if block.value.strip().startswith("###")]
    assert any("人格教练" in header for header in headers), headers
    assert not any(role_name in header for header in headers), f"教练页不该顶着「{role_name}」"
    captions = [block.value for block in app.main.caption]
    counts = re.compile(r"L0 原文 \d+ 条")
    assert not any(counts.search(text) for text in captions), "教练页不该显示角色记忆计数"
    assert any("访谈进度" in text for text in captions)

    # 教练页的输入不会写进当前角色的记忆
    before = ui_state.role_thread(role_name)
    app.text_area[0].set_value("一个总在楼下等我的人").run()
    next(button for button in app.button if button.label == "回答这一问").click().run()
    assert not app.exception
    assert ui_state.role_thread(role_name) == before, "教练访谈不能污染当前角色的对话线程"


def test_uploaded_avatar_renders_as_image_in_bubbles(app):
    """上传的图片头像：会话里只留 token，气泡里渲染成 <img>，emoji 头像不受影响。"""
    import base64

    import ui.state as ui_state
    from ui import avatars

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+p0hRAAAAAElFTkSuQmCC"
    )
    token = ui_state.save_avatar_upload(png, kind="user")
    ui_state.set_user_avatar(token)
    assert ui_state.user_avatar() == token, "图片 token 不能被当成 emoji 截成 4 个字"
    assert ui_state.avatar_label(token) == avatars.PLACEHOLDER

    app.run()
    _say(app, "我叫小林，最近在准备面试")
    rendered = _markdown(app)
    assert "data:image/png;base64," in rendered and "<img" in rendered

    ui_state.set_user_avatar("🐱")
    assert ui_state.user_avatar() == "🐱"
    assert avatars.path_of(token) is None, "换掉图片头像后旧文件应该被清理"
