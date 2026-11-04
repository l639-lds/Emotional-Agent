"""把 docs/答辩稿.md 排成「一页 A4」的 docs/答辩稿.docx。

用法：
    python docs/poster/make_docx.py           # 生成 docx 并报告版面估算
    python docs/poster/make_docx.py --check   # 只估算、不写文件

排版口径（与设计稿一致，改这里就等于改版式）：
    A4 21 x 29.7 cm，四边页边距 2cm；微软雅黑；正文 10.5pt、行距 1.0（微软雅黑单倍行高约 1.71em，实际行距 = 10.5pt x 1.714 ≈ 18pt，观感≈1.7em，仍宽松）；
    小节标题 11.5pt 加粗；文档标题 16pt 加粗；段前 0、段后 0（行距本身已留白）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt, RGBColor
from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[2]
MD_PATH = ROOT / "docs" / "答辩稿.md"
DOCX_PATH = ROOT / "docs" / "答辩稿.docx"

FONT = "微软雅黑"
FONT_FILE = Path("C:/Windows/Fonts/msyh.ttc")
BODY_PT = 10.5
SECTION_PT = 11.5
TITLE_PT = 16.0
LINE_MULT = 1.0
SPACE_AFTER_PT = 0.0
PAGE_W_CM, PAGE_H_CM = 21.0, 29.7
MARGIN_CM = 2.0
INK = RGBColor(0x1B, 0x24, 0x36)
ACCENT = RGBColor(0xA8, 0x32, 0x4A)
GREY = RGBColor(0x6B, 0x75, 0x85)


# ---------------------------------------------------------------------------
# 1) 解析 markdown：只取「能念出来的正文」，提词用的要点不要
# ---------------------------------------------------------------------------
def parse_md() -> list[tuple[str, str]]:
    """返回 [(kind, text)]，kind ∈ {title, section, para, note}。"""
    blocks: list[tuple[str, str]] = []
    for raw in MD_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.startswith(">"):
            continue
        if line.startswith("# "):
            blocks.append(("title", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(("section", line[3:].strip()))
        elif line.startswith("**要点**"):
            continue                                   # 提词提示，不进制式稿
        elif line.startswith("*（若现场"):
            blocks.append(("note", line.strip("*（）")))
        else:
            blocks.append(("para", line))
    return blocks


def plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")


# ---------------------------------------------------------------------------
# 2) 版面估算：用真实字体度量做断行，再按 Word 的行高口径累加
# ---------------------------------------------------------------------------
def _font(size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), int(round(size_pt * 2)))   # 2x 采样，除回去更准


def measure(text: str, size_pt: float) -> float:
    """返回文本宽度（pt）。"""
    f = _font(size_pt)
    return f.getlength(text) / 2.0


def wrap_lines(text: str, size_pt: float, width_pt: float) -> int:
    """贪心断行，返回行数。"""
    total = 0
    for chunk in text.split("\n"):
        if not chunk:
            total += 1
            continue
        line, count = "", 1
        for ch in chunk:
            if measure(line + ch, size_pt) > width_pt:
                count += 1
                line = ch
            else:
                line += ch
        total += count
    return total


def estimate(blocks: list[tuple[str, str]]) -> dict[str, float]:
    usable_pt = (PAGE_W_CM - 2 * MARGIN_CM) * 28.3465          # cm -> pt
    usable_h_pt = (PAGE_H_CM - 2 * MARGIN_CM) * 28.3465
    line_factor = 1.714                                         # 微软雅黑单倍行高（Word 实测：10.5pt/1.25 → 22.5pt 行距，反推 1.714）
    height = 0.0
    lines = 0
    for kind, text in blocks:
        size = TITLE_PT if kind == "title" else SECTION_PT if kind == "section" else BODY_PT
        n = wrap_lines(plain(text), size, usable_pt)
        lines += n
        height += n * size * LINE_MULT * line_factor
        if kind == "title":
            height += 4.0
        elif kind == "section":
            height += 4.0
        else:
            height += SPACE_AFTER_PT
    return {"lines": lines, "height_pt": height, "page_pt": usable_h_pt}


# ---------------------------------------------------------------------------
# 3) 生成 docx
# ---------------------------------------------------------------------------
def _east_asian(run, name: str = FONT) -> None:
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def _bottom_border(paragraph, color: str = "A8324A", size: int = 12) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    ppr.append(pbdr)


def add_runs(paragraph, text: str, size: float, color: RGBColor, bold_default: bool = False) -> None:
    """把 **加粗** 拆成 run。"""
    for i, piece in enumerate(re.split(r"(\*\*.+?\*\*)", text)):
        if not piece:
            continue
        bold = bold_default
        if piece.startswith("**") and piece.endswith("**"):
            piece = piece[2:-2]
            bold = True
        run = paragraph.add_run(piece.replace("`", ""))
        _east_asian(run)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color


def build(blocks: list[tuple[str, str]]) -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(PAGE_W_CM), Cm(PAGE_H_CM)
    for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(sec, side, Cm(MARGIN_CM))

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY_PT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    pf = normal.paragraph_format
    pf.line_spacing = LINE_MULT
    pf.space_before = Pt(0)
    pf.space_after = Pt(SPACE_AFTER_PT)

    for kind, text in blocks:
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = LINE_MULT
        if kind == "title":
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_after = Pt(1.5)
            add_runs(p, text, TITLE_PT, INK, bold_default=True)
            _bottom_border(p)
        elif kind == "section":
            p.paragraph_format.space_before = Pt(1.5)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.keep_with_next = True
            add_runs(p, text.replace("　", "  "), SECTION_PT, ACCENT, bold_default=True)
        elif kind == "note":
            p.paragraph_format.space_before = Pt(2)
            add_runs(p, text, 9.0, GREY)
        else:
            add_runs(p, text, BODY_PT, INK)
    return doc


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    blocks = parse_md()
    est = estimate(blocks)
    fill = est["height_pt"] / est["page_pt"] * 100
    spoken = "".join(plain(t) for k, t in blocks if k in ("para", "section", "title"))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", spoken))
    print(f"[docx] 段落 {len(blocks)} 个，估算 {est['lines']} 行 / "
          f"{est['height_pt'] / 28.3465:.1f} cm，占版心 {fill:.0f}%"
          f"（版心高 {(est['page_pt']) / 28.3465:.1f} cm）")
    print(f"[docx] 正文汉字 {cjk} 个")

    if "--check" in argv:
        return 0 if fill <= 100 else 1
    if fill > 100:
        print("[docx] 估算超过一页，请压缩内容或字号")

    doc = build(blocks)
    doc.save(DOCX_PATH)
    print(f"[docx] 已生成 {DOCX_PATH.relative_to(ROOT)}"
          f"（{DOCX_PATH.stat().st_size / 1024:.1f} KB，{len(doc.paragraphs)} 段）")
    return 0 if fill <= 100 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
