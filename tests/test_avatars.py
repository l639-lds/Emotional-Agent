"""头像上传 + 「填了 Key 就不再是演示模式」的回归测试。

这两个功能都属于"界面上看不见的约定"：
- 头像图必须落成本地文件、token 里不许出现路径，否则换头像会把几 MB 图片写进角色 JSON；
- 访客自己填的 Key 必须压过服务端的 ``EMO_DEMO_MODE=1``，否则他会看到
  "已使用你自己的 Key" 和 "离线演示模式" 同时出现（用户就是这么报的）。
"""

from __future__ import annotations

import base64

import pytest

from core import config as cfg
from core import llm
from ui import avatars

# 1×1 的合法 PNG（够 Pillow 识别、也够我们按文件头嗅探格式）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+p0hRAAAAAElFTkSuQmCC"
)
SAMPLE_KEY = "sk-abcdef1234567890abcdef1234567890"


@pytest.fixture(autouse=True)
def _isolated_avatar_dir(tmp_path, monkeypatch):
    """头像一律写进临时目录，别污染项目里的 data/avatars。"""
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path / "avatars")
    avatars._uri_cached.cache_clear()
    yield
    avatars._uri_cached.cache_clear()


def test_store_returns_lightweight_token_and_keeps_bytes_out_of_json():
    token = avatars.store(PNG_1PX, kind="user")

    assert token.startswith(avatars.AVATAR_PREFIX)
    assert len(token) < 40, "token 必须很短，角色/偏好 JSON 里只存它"
    assert avatars.is_image(token)
    path = avatars.path_of(token)
    assert path is not None and path.is_file()
    assert path.read_bytes() == PNG_1PX


def test_token_never_allows_path_traversal():
    for bad in ("img:../../etc/passwd", "img:..\\secret.png", "img:", "img:sub/dir.png"):
        assert avatars.path_of(bad) is None


def test_rejects_empty_unknown_and_oversized_files(monkeypatch):
    with pytest.raises(ValueError):
        avatars.store(b"", kind="user")
    with pytest.raises(ValueError):
        avatars.store(b"not-an-image-at-all", kind="user")
    monkeypatch.setattr(cfg, "AVATAR_MAX_BYTES", 8)
    with pytest.raises(ValueError):
        avatars.store(PNG_1PX, kind="user")


def test_html_avatar_inlines_image_and_escapes_emoji():
    token = avatars.store(PNG_1PX, kind="role", name="温柔老父亲")

    html = avatars.html_avatar(token)
    assert "<img" in html and "data:image/png;base64," in html

    assert avatars.html_avatar("🙂").count("🙂") == 1
    assert "<script" not in avatars.html_avatar("<script>alert(1)</script>")


def test_display_and_discard_cleanup():
    token = avatars.store(PNG_1PX, kind="user")
    assert avatars.display(token) == avatars.PLACEHOLDER

    assert avatars.discard(token) is True
    assert avatars.path_of(token) is None
    assert avatars.discard(token) is False, "重复删除不该抛异常"


def test_own_key_beats_server_side_forced_demo(monkeypatch):
    """访客自己填的 Key 优先于 EMO_DEMO_MODE=1（用户报的"两个文案打架"）。"""
    monkeypatch.setenv("EMO_DEMO_MODE", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    llm.set_session_api_key("")

    assert llm.forced_demo() is True
    assert llm.is_demo_mode() is True
    assert "EMO_DEMO_MODE" in llm.mode_label()

    llm.set_session_api_key(SAMPLE_KEY)
    try:
        assert llm.is_demo_mode() is False
        label = llm.mode_label()
        assert "在线模型" in label and "本会话" in label
        assert llm.key_fingerprint() == "sk-…7890"
        assert "7890" in label and SAMPLE_KEY not in label, "状态行只能有末 4 位"
    finally:
        llm.set_session_api_key("")


def test_placeholder_key_still_falls_back_to_demo(monkeypatch):
    monkeypatch.setenv("EMO_DEMO_MODE", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    llm.set_session_api_key("sk-xxxx")
    try:
        assert llm.is_demo_mode() is True, "占位符等于没填，继续走演示模式"
    finally:
        llm.set_session_api_key("")
