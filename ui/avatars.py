"""头像：要么是一个 emoji，要么是用户自己上传的一张图片。

设计取舍：
- 图片存在本地 ``data/avatars/``（和记忆文件同一层目录），**不上传任何地方**；
- 渲染时不走 ``st.image``（那会占掉一整行、也没法塞进自绘气泡），而是把图片压到
  ``AVATAR_PX`` 见方、裁成圆形用的正方形，再编码成 ``data:`` URI 内联进气泡 HTML。
  这样气泡是一次 markdown 画完的，不需要额外的静态文件服务；
- 会话里只存一个轻量 token（``img:<文件名>``），角色文件/偏好文件里也是这个 token，
  换头像不会把几 MB 的图片写进角色 JSON；
- 没有 Pillow 也能跑：退化成原图 base64（只对 ≤256KB 的小图生效），不至于整个界面崩掉。
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

from core import config as cfg

AVATAR_PREFIX = "img:"
PLACEHOLDER = "🖼"

# 只接受位图：SVG 可以带脚本，内联进页面等于执行任意代码，直接不收
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)
_WEBP_HEAD = (b"RIFF", b"WEBP")


def is_image(value: object) -> bool:
    """这个头像值是"图片 token"还是 emoji/文字。"""
    return isinstance(value, str) and value.strip().startswith(AVATAR_PREFIX)


def _sniff(data: bytes) -> str:
    """按文件头判断扩展名；不认识的返回空串。"""
    for head, ext in _MAGIC:
        if data.startswith(head):
            return ext
    if len(data) >= 12 and data[:4] == _WEBP_HEAD[0] and data[8:12] == _WEBP_HEAD[1]:
        return "webp"
    return ""


def store(data: bytes, *, kind: str, name: str = "") -> str:
    """存一张头像图，返回 token（``img:user_ab12cd34ef.png``）。

    Args:
        data: 上传文件的原始字节。
        kind: ``user`` 或 ``role``，只用于文件名前缀，方便自己翻目录。
        name: 可选的角色名，参与哈希，避免两个角色共用一张图时互相覆盖。

    Raises:
        ValueError: 文件为空 / 不是认识的图片格式 / 超过 ``AVATAR_MAX_BYTES``。
    """
    if not data:
        raise ValueError("这个文件是空的，换一张试试")
    if len(data) > cfg.AVATAR_MAX_BYTES:
        size = len(data) / 1024 / 1024
        raise ValueError(f"图片有 {size:.1f}MB，太大了，请压到 4MB 以内")
    ext = _sniff(data)
    if not ext:
        raise ValueError("只支持 PNG / JPG / GIF / WEBP 图片")
    digest = hashlib.sha1(data + (name or "").encode("utf-8")).hexdigest()[:10]
    filename = f"{kind or 'x'}_{digest}.{ext}"
    target = cfg.AVATAR_DIR / filename
    cfg.AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
    return f"{AVATAR_PREFIX}{filename}"


def path_of(value: object) -> Optional[Path]:
    """token → 本地文件路径（不存在或不是 token 时返回 None）。"""
    if not is_image(value):
        return None
    filename = str(value).strip()[len(AVATAR_PREFIX) :].strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None  # 只认文件名，挡住任何路径穿越
    target = cfg.AVATAR_DIR / filename
    return target if target.is_file() else None


def discard(value: object) -> bool:
    """删掉一个图片头像文件（换头像 / 删角色时顺手清理，失败就算了）。"""
    target = path_of(value)
    if target is None:
        return False
    try:
        target.unlink()
    except OSError:
        return False
    _uri_cached.cache_clear()
    return True


def _downscale(data: bytes) -> bytes:
    """压成 AVATAR_PX 见方的 PNG；没有 Pillow 就原样返回。"""
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:  # pragma: no cover - 只在没装 Pillow 的环境走到
        return data
    try:
        with Image.open(io.BytesIO(data)) as image:
            square = ImageOps.fit(image.convert("RGB"), (cfg.AVATAR_PX, cfg.AVATAR_PX))
            buffer = io.BytesIO()
            square.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:
        return data


@lru_cache(maxsize=64)
def _uri_cached(path_text: str, stamp: float) -> str:
    """(路径, mtime) → data URI；用 mtime 让"换了同一路径的图"也能刷新缓存。"""
    data = Path(path_text).read_bytes()
    small = _downscale(data)
    if small is data and len(data) > 256 * 1024:
        return ""  # 没 Pillow 又太大：宁可不显示，也不往页面里塞几 MB
    encoded = base64.b64encode(small).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def data_uri(value: object) -> str:
    """token → 可直接塞进 ``<img src=...>`` 的 data URI（拿不到就返回空串）。"""
    target = path_of(value)
    if target is None:
        return ""
    try:
        return _uri_cached(str(target), target.stat().st_mtime)
    except OSError:
        return ""


def display(value: object) -> str:
    """纯文本场合（标题、caption）用：图片就显示一个占位符号，emoji 原样返回。"""
    if is_image(value):
        return PLACEHOLDER if path_of(value) else "🙂"
    return str(value or "").strip() or "🙂"


def inline(value: object, *, size: int = 26) -> str:
    """标题行里用的小头像 HTML：图片走 <img>，emoji 原样（已转义）。"""
    uri = data_uri(value)
    if uri:
        return (
            f'<img src="{uri}" alt="" style="width:{size}px;height:{size}px;'
            f'border-radius:50%;object-fit:cover;vertical-align:-4px" />'
        )
    return html.escape(display(value), quote=False)


def html_avatar(value: object, *, css_class: str = "emo-avatar") -> str:
    """头像的 HTML：图片走 ``<img>``，其余当文字（已转义）。"""
    uri = data_uri(value)
    if uri:
        return f'<div class="{css_class}"><img src="{uri}" alt=""></div>'
    return f'<div class="{css_class}">{html.escape(display(value), quote=False)}</div>'
