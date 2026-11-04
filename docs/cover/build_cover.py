"""把 ``docs/cover/cover.html`` 渲染成 A4 竖版 PDF（顺带出一张 PNG 预览）。

用法::

    python docs/cover/build_cover.py

只用本机已装的 Chrome / Edge 做无头打印，不需要任何 Python 第三方依赖：

* ``--print-to-pdf``        出 PDF（A4 竖版、无页眉页脚）
* ``--screenshot``          出同名 PNG 预览，方便肉眼核对

改文案只改 ``cover.html``；改完跑一遍这个脚本，``cover.pdf`` 就跟着更新。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PDF = HERE / "cover.pdf"
OUT_PNG = HERE / "preview.png"
HTML = HERE / "cover.html"

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)


def find_browser() -> str:
    for candidate in BROWSERS:
        found = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
        if found and Path(found).exists():
            return str(found)
    raise SystemExit("没找到 Chrome / Edge，请手动指定浏览器路径后再跑。")


def run(browser: str, *args: str) -> None:
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"渲染失败（{result.returncode}）：{' '.join(cmd)}")


def main() -> int:
    if not HTML.exists():
        raise SystemExit(f"缺少 {HTML}")
    browser = find_browser()
    url = HTML.as_uri()
    profile = Path(tempfile.gettempdir()) / "emo-cover-profile"
    common = (f"--user-data-dir={profile}", "--virtual-time-budget=6000")

    run(
        browser,
        *common,
        "--no-pdf-header-footer",
        f"--print-to-pdf={OUT_PDF}",
        url,
    )
    run(
        browser,
        *common,
        "--window-size=794,1123",
        "--force-device-scale-factor=2",
        f"--screenshot={OUT_PNG}",
        url,
    )
    for path in (OUT_PDF, OUT_PNG):
        print(f"{path.name}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
