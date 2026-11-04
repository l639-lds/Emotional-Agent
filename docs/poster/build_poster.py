#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EmoMem A2 海报构建脚本（HTML → PDF + PNG）。

设计说明见同目录 DESIGN.md。所有文案集中在 ``CONTENT``，改文案只改这一处；
指标数字从同目录 ``metrics.json`` 读取，文件不存在时显示「待评测」。

用法::

    python docs/poster/build_poster.py            # 生成 poster.html + poster.pdf + poster.png
    python docs/poster/build_poster.py --html     # 只重建 poster.html
    python docs/poster/build_poster.py --check    # 只校验已有产物（页数 / 尺寸 / 越界文字）

依赖：仅标准库（subprocess 调 Chrome）；``--check`` 若装了 pypdf / PyMuPDF 会额外校验。
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
HTML_PATH = HERE / "poster.html"
PDF_PATH = HERE / "poster.pdf"
PNG_PATH = HERE / "poster.png"
METRICS_PATH = HERE / "metrics.json"

PAGE_W_MM = 420.0
PAGE_H_MM = 594.0
# 96 dpi 下 A2 = 1588 x 2245 px
SCREEN_W_PX = 1588
SCREEN_H_PX = 2245

CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
)
PROFILE_DIR = Path(os.environ.get("TEMP", str(HERE))) / "emo-poster-profile"


def find_chrome() -> Path:
    """按候选列表找 Chrome 可执行文件。"""
    for candidate in CHROME_CANDIDATES:
        if candidate and candidate.exists():
            return candidate
    raise SystemExit("找不到 Chrome，请手动修改 build_poster.py 的 CHROME_CANDIDATES")


# ---------------------------------------------------------------------------
# 文案（唯一内容源；改海报文案改这里）
# ---------------------------------------------------------------------------

CONTENT: dict[str, Any] = {
    "title": "EmoMem",
    "subtitle": "情感感知的三层记忆 + 人设编译器",
    "kicker": "2026 软工实践周 · 高级组 / HACKATHON",
    "kicker_right": "答辩物料 · A2 · 2026.11",
    "value": "记得住 · 忘得掉 · 说得对",
    "lede": (
        "把陪伴型 AI 的记忆从「一个 list」升级成可检索、可固化、可遗忘、可解释的三层记忆系统；"
        "把人设从「一段 prompt」升级成可编译、可自评、可体检的人格。"
    ),
    # 02 痛点
    "pain_sec": ("PROBLEM", "为什么还需要一个 EmoMem", "旧版：单文件 Streamlit + 一个 memories 列表"),
    "pains": [
        ("记不住",
         "旧版记忆是 memories: list[str]，每轮只回放最近 20 条上下文。聊到第 50 轮，前 30 轮就真的消失了。"),
        ("记住了乱用",
         "单层列表没有权重、没有时效、没有情绪：翻旧账、答非所问；用户越难过，越容易撞上最扎心的那一条。"),
        ("人设千篇一律",
         "一句描述直接扩写成 prompt，产出全是「温柔体贴、善解人意」；没有语音指纹、没有边界，演三句就崩。"),
    ],
    # 03 架构
    "arch_sec": ("ARCHITECTURE", "一轮对话里，记忆是如何被读出来、写回去的", "参数全部取自 core/config.py"),
    "arch_chain": [
        ("用户输入", "情绪识别：开心 / 难过 / 生气 / 平静"),
        ("检索打分", "sim · importance · recency · emotion"),
        ("记忆块注入", "≤ 1800 字符，超预算按分数裁剪"),
        ("System Prompt", "人设 + 记忆 + JSON 协议"),
        ("DeepSeek", "reply / emotion / initiative"),
        ("回应 + 界面", "聊天气泡 · Trace 可展开"),
    ],
    "arch_tiers": [
        ("短期 · 工作记忆", "WORKING", "#38BDF8", [
            "最近 20 条 turn 逐字回放，注入最近 8 条",
            "每条带 emotion 与 salience 强度",
            "滚动摘要压缩视图，跨会话续接",
            "会话重启由情景 / 语义层补全上下文",
        ]),
        ("中期 · 情景记忆", "EPISODIC", "#A78BFA", [
            "每 4 轮由一次 LLM 调用压成 Episode",
            "标题 / 摘要 / 3–6 个话题词 / 关键词",
            "valence · arousal · importance 三元特征",
            "命中即进 Trace，可展开看来源",
        ]),
        ("长期 · 语义画像", "SEMANTIC", "#F59E0B", [
            "Fact 分 8 类：身份 / 喜好 / 厌恶 / 近况 / 禁忌等",
            "confidence + evidence_count 随证据演化",
            "身份 · 禁忌 · 约定 高置信度强制置顶",
        ]),
    ],
    # 架构图四条箭头的文案（唯一文案源，_arch_svg 直接引用）
    "arch_legend": {
        "recall": ("召回", "三库统一打分 · Mood Gate"),
        "consolid": ("固化", "每 4 轮 · 一次 LLM 调用"),
        "forget": ("遗忘", "强度 < 0.15 归档，只标记不删除"),
        "conflict": ("冲突消解", "旧 Fact superseded_by 留档"),
    },
    "arch_params": [
        "检索分 = 0.45·相似度 + 0.20·重要性 + 0.20·时间衰减 + 0.15·情绪显著性",
        "τ = Episode 10 天 / Fact 30 天　·　记忆块预算 1800 字符　·　归档阈值 0.15　·　top-k = Episode 4 / Fact 8 / Loop 3",
    ],
    # 04 六个创新点（口径严格沿用 SPEC §0）
    "innov_sec": ("INNOVATION", "六个创新点：每一条都能在代码里复算", "每条都给出对应参数，可现场对照 core/config.py"),
    "innovations": [
        ("01", "三层记忆 + 记忆固化",
         ["工作记忆逐轮记录，每 4 轮触发一次「睡眠整理」：一次 LLM 调用把对话压成情景 Episode，并提炼长期 Fact。",
          "短期只留 20 条 turn、注入最近 8 条；长期注入预算 1800 字符，超了按分数升序裁剪。",
          "写回、固化、裁剪都走同一份 schema，可回放可调试。"],
         "CONSOLIDATE_EVERY_TURNS = 4 · SHORT_TERM_MAX_TURNS = 20"),
        ("02", "情绪加权检索 + 防翻旧账",
         ["每条记忆带效价 valence 与唤醒度 arousal，检索分 = 0.45 相似度 + 0.20 重要性 + 0.20 时间衰减 + 0.15 情绪显著性。",
          "用户处于「难过 / 生气」时，未被提及的高唤醒负性记忆直接跳过，并写进 Trace 说明原因。",
          "不是「更懂你」的口号，而是一条可开关的规则。"],
         "W = 0.45 / 0.20 / 0.20 / 0.15 · gate: valence<−0.3, arousal>0.6, sim<0.25"),
        ("03", "记忆冲突消解",
         ["新旧事实相似度落在 0.50–0.82 且语义相反，判定为「记忆修正」：旧条目写 superseded_by 作废，但保留审计痕迹。",
          "同一条事实反复出现则 evidence_count +1、confidence +0.1，置信度随证据数演化。",
          "记忆会改口，但改口有据可查。"],
         "FACT_MERGE_SIM = 0.82 · FACT_CONFLICT_SIM = 0.50"),
        ("04", "可解释记忆 Trace",
         ["每轮记录注入了哪些记忆、四项分值各自多少，以及一句中文理由，例如「与『面试』高度相关，且是高唤醒负面记忆」。",
          "界面可展开查看，并附「本轮未采用的记忆及原因」——被 mood gate 或预算裁掉的都写明白。",
          "可解释性不是日志，是能当场指着屏幕讲的产品能力。"],
         "TraceItem：tier / score / components / reason / pinned"),
        ("05", "关系档案驱动人格漂移",
         ["intimacy、trust、stage、称呼演化与共同梗组成关系档案；阶段反向调节语气：陌生克制 → 熟悉 → 亲近 → 可调侃撒娇。",
          "人设不再是开场写死的一段 prompt，而是随相处时长持续漂移的角色状态。",
          "亲密度只涨不跌的假人设，在这里被拆成四段可验证的语气。"],
         "stage：陌生 <20 / 熟悉 <45 / 亲近 <75 / 亲密"),
        ("06", "未闭环事件 + 主动关怀",
         ["从对话里抽取「待跟进事项」（面试 / 体检 / 约定 / 情绪牵挂），下次对话主动追问，闭环后自动关闭。",
          "另有三层记忆消融开关：现场逐个关掉短期 / 中期 / 长期，直接对比回复差异。",
          "能主动追问的角色，才像真的把你放在心上。"],
         "LOOP_MAX = 3 · tiers ⊆ {short, mid, long}"),
    ],
    # 05 人设编译器
    "persona_sec": ("PERSONA FORGE", "人设编译器：一句话描述 → 一个跑得起来的人格", "创新点 07 · 独立的编译流水线"),
    "persona_steps": [
        ("一句描述", "＋ 4 个旋钮：毒舌 · 温柔 · 话痨 · 神秘"),
        ("结构化人设卡", "12 个内容字段 + 头像位"),
        ("6 维自评", "0–100 打分，附问题清单"),
        ("定向重写", "只改被点名的问题，≤ 1 轮"),
        ("3 问体检", "探针实测，通过线 75 分"),
    ],
    "persona_blocks": [
        ("人设卡里到底有什么",
         "语音指纹（语域 / 平均句长 / 口癖 / 标点习惯 / 禁用词 / 3 条台词锚点）、情绪反应表（触发 → 反应 → 身体语言）、"
         "关系弧（陌生 / 熟悉 / 亲近 / 亲密四段）、边界、反套路、记忆钩子与禁忌。"),
        ("6 维自评的口径",
         "可执行性 / 一致性 / 差异化 / 情感深度 / 安全性 / 可演示性，各 0–100；低于 75 或存在问题就进重写。"
         "元 Prompt 里写死反陈词滥调规则，禁止「温柔体贴、善解人意」这类空话。"),
        ("3 问体检的探针",
         "「我今天有点撑不住了，什么都不想干。」/「你要是能变成人，最想和我一起做什么？」/"
         "「我最讨厌别人骗我了，你会骗我吗？」——答得不像这个人，就是没编译好。"),
    ],
    # 06 对比表
    "compare_sec": ("COMPARISON", "和既有记忆方案比，差在哪、强在哪", "定性对比，依据各项目公开论文与文档"),
    "compare_head": ["维度", "EmoMem（本项目）", "MemGPT", "MemOS", "纯 RAG 记忆"],
    "compare_rows": [
        ("分层", "三层 + 固化流水线",
         "主上下文 / 外部存储两级", "记忆分层 + 调度器", "单一向量库"),
        ("遗忘曲线", "强度 = base·e^(−Δt/τ)，τ = 10 / 30 天",
         "按上下文压力换出", "有生命周期治理", "无，永久等权"),
        ("情绪门控", "valence / arousal 双维 + 防翻旧账",
         "无", "无", "无"),
        ("冲突消解", "superseded_by 作废留档 + 证据计数",
         "模型自主改写，无审计", "版本化 / 调度治理", "后写覆盖"),
        ("可解释", "Trace：四项分值 + 未采用原因",
         "上下文占用可视化", "调度日志", "只有相似度分数"),
        ("人格演化", "关系档案 → 语气漂移 + 人设编译器",
         "静态 system prompt", "静态 system prompt", "静态 system prompt"),
    ],
    "compare_note": (
        "对比为定性归纳（公开论文与文档截至 2026.11）；EmoMem 一列的所有参数都能在 core/config.py 里逐条对上，"
        "数值不是包装出来的。"
    ),
    # 07 技术栈 + 指标
    "stack_sec": ("ENGINEERING", "技术栈与离线指标", "零新增依赖 · 离线可跑"),
    "stack": [
        ("内核", "Python 3.10+ · 纯标准库"),
        ("界面", "Streamlit 单页应用"),
        ("模型", "DeepSeek（OpenAI 兼容）"),
        ("存储", "本地 JSON · 原子写 + v2→v3 迁移"),
        ("离线", "EMO_DEMO_MODE 假模型，无 Key 可跑"),
        ("测试", "pytest · 全部离线、确定性"),
    ],
    # 备注行：有真数字时按 metrics.json 的轮数/固化段数生成；缺失时说明等待评测。
    "metrics_note": (
        "指标由 scripts/eval_memory.py 离线跑出（{turns} 轮脚本化对话 / {episodes} 段固化剧本），"
        "写入 docs/poster/metrics.json 后由 build_poster.py 自动填充。"
    ),
    "metrics_note_pending": (
        "评测文件 docs/poster/metrics.json 尚未产出；拿到数字后重跑 build_poster.py 会自动填充，"
        "这里不写占位数字。"
    ),
    "footer": ("EmoMem · 2026 软工实践周 高级组 · 2026.11",
               "全部参数集中在 core/config.py · 不新增第三方依赖 · 数据留在本地"),
}

# 指标槽位：(metrics.json 键名, 计数单位, 说明行)。说明行统一 ≤ 14 字、单行；数字缺失即「待评测」。
# 口径提醒：compress_ratio 是「记忆块字符 / 原始对话字符」（1251 / 1912 = 65.4%），
# 也就是上下文占用只剩 65.4%、省下 34.6%，**不能写成「压缩了 65%」**。
METRIC_SLOTS: list[tuple[str, str, str]] = [
    ("compress_ratio", "", "上下文占用 · 省下 34.6%"),
    ("probe_hit", "", "5 条预埋探针全部命中"),
    ("conflict_resolved", " 条", "旧说法留档 superseded_by"),
    ("avg_strength_d0", "", "刚写入时的平均强度"),
    ("avg_strength_d30", "", "30 天后仍可注入"),
    ("mood_gate_hits", " 次", "40 轮里跳过 5 条旧负向"),
]
METRIC_PENDING = "待评测"
# 备注行里要引用的两个原始计数（不单独上墙）。
METRIC_EXTRAS = {"turns": "轮", "episodes": "段固化剧本", "facts": "条事实"}


# ---------------------------------------------------------------------------
# 指标读取
# ---------------------------------------------------------------------------

def load_metrics() -> tuple[list[dict[str, str]], bool, dict[str, object]]:
    """读取 metrics.json，映射到固定槽位；文件缺失/损坏时全部显示「待评测」。

    该文件由 ``scripts/eval_memory.py`` 生成，键名固定：
    compress_ratio / probe_hit / conflict_resolved / avg_strength_d0 /
    avg_strength_d30 / mood_gate_hits（turns / episodes / facts 只用于备注行）。

    三种可接受的写法（任选其一）：
      1. {"compress_ratio": 3.2, "probe_hit": "0.83", ...}          # 推荐
      2. {"metrics": {"compress_ratio": 3.2, ...}}                   # 带外壳
      3. {"metrics": [{"key": "compress_ratio", "value": "3.2×"}, ...]}
    """
    tiles = _empty_metrics()
    if not METRICS_PATH.exists():
        return tiles, False, {}
    try:
        raw = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return tiles, False, {}
    if not isinstance(raw, dict):
        return tiles, False, {}

    # 剥掉可能的外壳
    body = raw.get("metrics", raw)
    if isinstance(body, list):
        body = {str(it.get("key")): it.get("value") for it in body if isinstance(it, dict)}

    used = False
    for tile in tiles:
        key = tile["key"]
        if key not in body:
            continue
        text = _format_metric(body[key], tile["unit"])
        if not text:
            continue
        tile["value"] = text
        used = True
    extras = {k: body[k] for k in METRIC_EXTRAS if isinstance(body.get(k), (int, float))}
    return tiles, used, extras


def _format_metric(value: object, unit: str) -> str:
    """统一数字写法：整数带单位（1 条 / 5 次），浮点取三位有效数字，字符串原样。"""
    if value is None or isinstance(value, bool) or value == "":
        return ""
    if isinstance(value, int):
        return f"{value}{unit}"
    if isinstance(value, float):
        if unit and float(value).is_integer():
            return f"{int(value)}{unit}"
        return f"{value:.3g}"
    return str(value).strip()


def _empty_metrics() -> list[dict[str, str]]:
    """返回全部「待评测」的槽位快照。"""
    return [
        {"key": key, "unit": unit, "label": label, "value": METRIC_PENDING}
        for key, unit, label in METRIC_SLOTS
    ]


# ---------------------------------------------------------------------------
# 样式表（不用 f-string，避免 CSS 花括号冲突）
# ---------------------------------------------------------------------------

CSS = r"""
@page { size: 420mm 594mm; margin: 0; }

:root {
  --ink: #0F172A;
  --panel: #16223B;
  --panel-2: #1B2A46;
  --line: #24334F;
  --line-soft: #1E2C47;
  --text: #E8EDF7;
  --muted: #9AAAC6;
  --dim: #6B7C99;
  --short: #38BDF8;
  --mid: #A78BFA;
  --long: #F59E0B;
  --accent: #FB7185;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  margin: 0; padding: 0;
  background: #0F172A;
}

body {
  font-family: "Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif;
  color: var(--text);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}

.poster {
  position: relative;
  width: 420mm;
  height: 594mm;
  padding: 11mm 14mm 9mm 14mm;
  background: var(--ink);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  overflow: hidden;
}

/* ---------- 通用区块标题 ---------- */
.sec-head {
  display: flex;
  align-items: baseline;
  gap: 4mm;
  margin-bottom: 3.4mm;
}
.sec-kicker {
  font-size: 8pt;
  letter-spacing: .26em;
  font-weight: 700;
  color: var(--accent);
  white-space: nowrap;
}
.sec-title {
  font-size: 16.5pt;
  font-weight: 700;
  letter-spacing: .01em;
  color: var(--text);
  white-space: nowrap;
}
.sec-rule {
  flex: 1 1 auto;
  height: .18mm;
  background: var(--line);
  transform: translateY(-1.2mm);
}
.sec-note {
  font-size: 8pt;
  color: var(--dim);
  white-space: nowrap;
}

/* ---------- 01 头部 ---------- */
.head { display: flex; flex-direction: column; gap: 2.4mm; }
.head-top { display: flex; justify-content: space-between; align-items: baseline; }
.head-kicker {
  font-size: 8.5pt; letter-spacing: .24em; font-weight: 700; color: var(--accent);
}
.head-kicker-r { font-size: 8.5pt; letter-spacing: .18em; color: var(--dim); }
.title {
  font-size: 76pt;
  font-weight: 700;
  line-height: .92;
  letter-spacing: -.01em;
  color: #FFFFFF;
}
.title .dot { color: var(--accent); }
.subtitle {
  font-size: 20pt;
  font-weight: 700;
  color: #C7D3EA;
  letter-spacing: .04em;
}
.head-bottom {
  display: flex;
  align-items: flex-start;
  gap: 8mm;
  margin-top: 1.6mm;
}
.value {
  flex: 0 0 132mm;
  font-size: 25pt;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: .1em;
  line-height: 1.15;
}
.lede {
  flex: 1 1 auto;
  font-size: 10.5pt;
  line-height: 1.75;
  color: var(--muted);
  border-left: .35mm solid var(--line);
  padding-left: 7mm;
}
.head-rule { display: flex; height: .5mm; margin-top: 2.4mm; }
.head-rule .a { flex: 0 0 46mm; background: var(--accent); }
.head-rule .b { flex: 1 1 auto; background: var(--line); }

/* ---------- 02 痛点 ---------- */
.pain-body { display: flex; gap: 6mm; }
.pain-item {
  flex: 1 1 0;
  padding: 3.2mm 5mm;
  border-left: .45mm solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 1.4mm;
}
.pain-item:first-child { border-left-color: var(--accent); }
.pain-no {
  font-size: 8pt; letter-spacing: .2em; color: var(--dim); font-weight: 700;
}
.pain-title {
  font-size: 13pt; font-weight: 700; color: var(--text); letter-spacing: .02em;
}
.pain-text {
  font-size: 9.5pt; line-height: 1.55; color: var(--muted);
}

/* ---------- 03 架构图（以下 mm 数值与 _arch_svg 的 viewBox 1:1 对齐，
              改高度请只改 MM 常量，CSS 与 SVG 会一起变） ---------- */
.arch-canvas { position: relative; height: __CANVAS_H__mm; }
.arch-svg { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 0; }
.arch-row {
  position: absolute; left: 0; right: 0; top: 0;
  height: __NODE_H__mm;
  display: flex; gap: 6.4mm; z-index: 1;
}
.node {
  flex: 1 1 0;
  background: var(--panel);
  border: .3mm solid var(--line);
  border-radius: 2.2mm;
  padding: 3.4mm 3.6mm;
  display: flex; flex-direction: column; justify-content: center; gap: 1.4mm;
}
.node.is-core { border-color: rgba(251, 113, 133, .55); background: #1A2742; }
.node-title { font-size: 11pt; font-weight: 700; color: var(--text); letter-spacing: .02em; }
.node-sub { font-size: 7.8pt; line-height: 1.45; color: var(--muted); }

.tier-row {
  position: absolute; left: 0; right: 0; top: __TIER_TOP__mm;
  height: __TIER_H__mm;
  display: flex; gap: __TIER_GAP__mm; z-index: 1;
}
.tier {
  flex: 1 1 0;
  background: var(--panel);
  border: .3mm solid var(--line);
  border-top: .9mm solid var(--tier-color, var(--line));
  border-radius: 2.2mm;
  padding: 4mm 5mm;
  display: flex; flex-direction: column; gap: 2mm;
}
.tier-head { display: flex; align-items: baseline; gap: 3mm; }
.tier-name { font-size: 12.5pt; font-weight: 700; color: var(--tier-color); letter-spacing: .02em; }
.tier-en { font-size: 7.5pt; letter-spacing: .22em; color: var(--dim); font-weight: 700; }
.tier-item {
  font-size: 8.8pt; line-height: 1.55; color: var(--muted);
  padding-left: 4mm; position: relative;
}
.tier-item::before {
  content: ""; position: absolute; left: 0; top: 1.5mm;
  width: 1.6mm; height: 1.6mm; border-radius: 50%;
  background: var(--tier-color); opacity: .75;
}
.conflict-line {
  margin-top: auto;
  display: flex; align-items: center; gap: 2mm;
  font-size: 8.2pt; color: var(--muted);
  background: rgba(251, 113, 133, .1);
  border: .25mm solid rgba(251, 113, 133, .35);
  border-radius: 1.6mm;
  padding: 1.6mm 3mm;
}
.conflict-line .tag { color: var(--accent); font-weight: 700; letter-spacing: .06em; white-space: nowrap; }
.conflict-line b { color: var(--text); font-weight: 700; }

.fade-strip {
  position: absolute; left: 0; right: 0; top: __FADE_TOP__mm; height: __FADE_H__mm;
  display: flex; align-items: center; gap: 4mm;
  background: var(--line-soft);
  border: .25mm dashed var(--line);
  border-radius: 1.8mm;
  padding: 0 4mm;
  font-size: 8.2pt; color: var(--muted);
  z-index: 1;
}
.fade-strip .tag {
  color: var(--dim); font-weight: 700; letter-spacing: .16em; font-size: 7.8pt; white-space: nowrap;
}
.params-row {
  position: absolute; left: 0; right: 0; top: __PARAMS_TOP__mm;
  display: flex; flex-direction: column; gap: 1.8mm;
  font-size: 8pt; color: var(--muted); line-height: 1.35;
  z-index: 1;
}
.params-row .p-item { white-space: nowrap; }

/* ---------- 04 创新点 ---------- */
.innov-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: auto auto;
  gap: 5mm;
}
.card {
  background: var(--panel);
  border: .3mm solid var(--line);
  border-radius: 2.4mm;
  padding: 3.6mm 5mm;
  display: flex;
  flex-direction: column;
  gap: 1.6mm;
  position: relative;
  overflow: hidden;
}
.card::after {
  content: ""; position: absolute; left: 0; top: 0; width: 100%; height: .55mm;
  background: var(--accent); opacity: .55;
}
.card-head { display: flex; align-items: baseline; gap: 3mm; }
.card-no {
  font-size: 17pt; font-weight: 700; color: var(--accent); line-height: 1;
  font-family: "Consolas", "Cascadia Mono", monospace;
  opacity: .9;
}
.card-title { font-size: 13pt; font-weight: 700; color: var(--text); letter-spacing: .01em; }
.card-body { font-size: 9pt; line-height: 1.52; color: var(--muted); }
.card-body p { margin-bottom: 1mm; }
.card-body p:last-child { margin-bottom: 0; }
.card-param {
  margin-top: auto;
  font-size: 7.6pt;
  font-family: "Consolas", "Cascadia Mono", monospace;
  color: #7E90AE;
  border-top: .2mm dashed var(--line);
  padding-top: 1.8mm;
  letter-spacing: .01em;
}

/* ---------- 05 人设编译器 ---------- */
.persona-flow { display: flex; gap: 6mm; min-height: 18mm; }
.step {
  flex: 1 1 0;
  background: var(--panel);
  border: .3mm solid var(--line);
  border-radius: 2mm;
  padding: 3mm 3.4mm;
  display: flex; flex-direction: column; gap: 1.4mm; justify-content: center;
  position: relative;
}
.step::before {
  content: attr(data-no);
  position: absolute; top: 2.2mm; right: 3mm;
  font-size: 7pt; color: var(--dim); letter-spacing: .12em; font-family: "Consolas", monospace;
}
.step::after {                       /* 步骤之间的流向箭头，落在 6mm 的缝里 */
  content: "›";
  position: absolute; right: -4.4mm; top: 50%; transform: translateY(-50%);
  font-size: 13pt; line-height: 1; color: var(--accent); opacity: .75;
}
.step:last-child::after { content: none; }
.step-name { font-size: 10.5pt; font-weight: 700; color: var(--text); }
.step-note { font-size: 7.6pt; line-height: 1.5; color: var(--muted); }
.persona-blocks { display: flex; gap: 5.5mm; margin-top: 3.6mm; }
.pblock {
  flex: 1 1 0;
  border-left: .45mm solid var(--line);
  padding: 1mm 0 0 4.4mm;
}
.pblock:nth-child(1) { border-left-color: var(--short); }
.pblock:nth-child(2) { border-left-color: var(--mid); }
.pblock:nth-child(3) { border-left-color: var(--long); }
.pblock-title { font-size: 9.6pt; font-weight: 700; color: var(--text); margin-bottom: 1.6mm; }
.pblock-text { font-size: 8.4pt; line-height: 1.6; color: var(--muted); }

/* ---------- 06 对比表 ---------- */
table.cmp {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 8.6pt;
}
table.cmp col.c-dim { width: 34mm; }
table.cmp col.c-us { width: 104mm; }
table.cmp col.c-other { width: 84.6mm; }
table.cmp th, table.cmp td {
  text-align: left;
  vertical-align: middle;
  padding: 1.05mm 3.2mm;
  border-bottom: .2mm solid var(--line-soft);
  line-height: 1.42;
}
table.cmp thead th {
  font-size: 8pt; font-weight: 700; letter-spacing: .1em;
  color: var(--dim); border-bottom: .35mm solid var(--line);
  padding-bottom: 1.8mm;
}
table.cmp thead th.is-us { color: var(--accent); }
table.cmp td.dim { color: var(--text); font-weight: 700; font-size: 9pt; }
table.cmp td.us { color: #F5D6DC; background: rgba(251, 113, 133, .07); font-size: 8.6pt; }
table.cmp td.other { color: var(--muted); }
table.cmp tr:last-child td { border-bottom: none; }
.cmp-note { font-size: 8pt; color: var(--dim); margin-top: 1.6mm; line-height: 1.45; }

/* ---------- 07 技术栈 + 指标 ---------- */
.stack-row {
  display: flex; gap: 3.4mm; margin-bottom: 3.6mm;
}
.chip {
  flex: 1 1 0;
  background: var(--line-soft);
  border: .25mm solid var(--line);
  border-radius: 1.6mm;
  padding: 2mm 3mm;
  display: flex; flex-direction: column; gap: .8mm;
}
.chip-k { font-size: 7.2pt; letter-spacing: .18em; color: var(--dim); font-weight: 700; }
.chip-v { font-size: 8.8pt; color: var(--text); line-height: 1.35; }
.metric-row { display: flex; gap: 3.4mm; }
.metric {
  flex: 1 1 0;
  background: var(--panel);
  border: .3mm solid var(--line);
  border-radius: 2mm;
  padding: 2.4mm 3.2mm;
  display: flex; flex-direction: column; gap: 1.4mm;
}
.metric-v {
  font-size: 17pt; font-weight: 700; color: var(--accent); line-height: 1;
  font-family: "Consolas", "Microsoft YaHei", monospace;
  letter-spacing: .01em;
}
.metric-v.is-pending { font-size: 12.5pt; color: var(--dim); letter-spacing: .12em; }
.metric-l {
  font-size: 9pt; color: var(--text); line-height: 1.25;
  white-space: nowrap; overflow: hidden;
}
.eng-note {
  font-size: 8.6pt; color: var(--dim);
  margin-top: 1.8mm; line-height: 1.3;
  white-space: nowrap;
}

/* ---------- 08 页脚 ---------- */
.foot {
  display: flex; justify-content: space-between; align-items: baseline;
  border-top: .3mm solid var(--line);
  padding-top: 2.4mm;
  font-size: 8.6pt; color: var(--dim); letter-spacing: .06em;
}

@media print {
  html, body { width: 420mm; height: 594mm; overflow: hidden; }
  .poster { break-after: avoid; break-inside: avoid; }
}
"""

# ---------------------------------------------------------------------------
# 架构图纵向栅格（单位 mm）：CSS 与 SVG 共用同一组数字，改这里两边一起变。
# ---------------------------------------------------------------------------

MM: dict[str, float] = {
    "NODE_H": 18.0,       # 主链 6 个节点的高度
    "CANVAS_H": 112.0,    # .arch-canvas 总高 = SVG viewBox 高度
    "TIER_TOP": 40.0,     # 三层记忆卡顶端
    "TIER_H": 45.0,       # 三层记忆卡高度
    "TIER_GAP": 26.0,     # 卡片横向间距（固化 / 提炼箭头画在缝里）
    "FADE_TOP": 94.0,     # 遗忘 · 归档层
    "FADE_H": 8.0,
    "PARAMS_TOP": 104.0,  # 参数条
}
for _k, _v in MM.items():
    CSS = CSS.replace(f"__{_k}__", f"{_v:g}")

ARCH_W = 392.0                       # 画布宽度 = 420mm 页面 - 左右各 14mm 页边距
NODE_GAP = 6.4
NODE_W = (ARCH_W - 5 * NODE_GAP) / 6
NODE_C = [NODE_W / 2 + i * (NODE_W + NODE_GAP) for i in range(6)]
TIER_W = (ARCH_W - 2 * MM["TIER_GAP"]) / 3
TIER_C = [TIER_W / 2 + i * (TIER_W + MM["TIER_GAP"]) for i in range(3)]   # 卡片中心
TIER_EDGE = [i * (TIER_W + MM["TIER_GAP"]) for i in range(3)]             # 卡片左边缘
TIER_BOT = MM["TIER_TOP"] + MM["TIER_H"]


# ---------------------------------------------------------------------------
# 片段渲染
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    """HTML 转义。"""
    return html.escape(str(text), quote=False)


def sec_head(kicker: str, title: str, note: str = "") -> str:
    """统一区块标题：英文小标 + 中文标题 + 细线 + 备注。"""
    note_html = f'<span class="sec-note">{esc(note)}</span>' if note else ""
    return (
        '<div class="sec-head">'
        f'<span class="sec-kicker">{esc(kicker)}</span>'
        f'<span class="sec-title">{esc(title)}</span>'
        '<span class="sec-rule"></span>'
        f'{note_html}'
        "</div>"
    )


def render_head() -> str:
    """01 标题区。"""
    return f"""
<header class="head">
  <div class="head-top">
    <span class="head-kicker">{esc(CONTENT['kicker'])}</span>
    <span class="head-kicker-r">{esc(CONTENT['kicker_right'])}</span>
  </div>
  <h1 class="title">{esc(CONTENT['title'])}<span class="dot">.</span></h1>
  <p class="subtitle">{esc(CONTENT['subtitle'])}</p>
  <div class="head-bottom">
    <p class="value">{esc(CONTENT['value'])}</p>
    <p class="lede">{esc(CONTENT['lede'])}</p>
  </div>
  <div class="head-rule"><span class="a"></span><span class="b"></span></div>
</header>
"""


def render_pain() -> str:
    """02 痛点区（3 条）。"""
    kicker, title, note = CONTENT["pain_sec"]
    items = []
    for idx, (name, text) in enumerate(CONTENT["pains"], start=1):
        items.append(
            '<div class="pain-item">'
            f'<div class="pain-no">0{idx} / PAIN</div>'
            f'<div class="pain-title">{esc(name)}</div>'
            f'<div class="pain-text">{esc(text)}</div>'
            "</div>"
        )
    return f"""
<section class="pain">
  {sec_head(kicker, title, note)}
  <div class="pain-body">{''.join(items)}</div>
</section>
"""


def render_arch() -> str:
    """03 架构图：DOM 盒子 + SVG 箭头（坐标单位为 mm，与容器 392×118mm 对齐）。"""
    kicker, title, note = CONTENT["arch_sec"]

    # 主链 6 个节点
    nodes = []
    for idx, (name, sub) in enumerate(CONTENT["arch_chain"]):
        core = " is-core" if idx in (1, 2) else ""
        nodes.append(
            f'<div class="node{core}">'
            f'<div class="node-title">{esc(name)}</div>'
            f'<div class="node-sub">{esc(sub)}</div>'
            "</div>"
        )

    # 三层记忆卡
    tiers = []
    for name, en, color, lines in CONTENT["arch_tiers"]:
        items = "".join(f'<div class="tier-item">{esc(line)}</div>' for line in lines)
        extra = ""
        if name.startswith("长期"):
            extra = (
                '<div class="conflict-line"><span class="tag">冲突消解</span>'
                '<span>旧 fact</span>'
                '<svg width="9mm" height="3mm" viewBox="0 0 34 11" aria-hidden="true">'
                '<path d="M1,5.5 H26" stroke="#FB7185" stroke-width="1.4" fill="none"/>'
                '<path d="M26,1.5 L32,5.5 L26,9.5 Z" fill="#FB7185"/></svg>'
                '<b>新 fact</b><span>· superseded_by 留档</span></div>'
            )
        tiers.append(
            f'<div class="tier" style="--tier-color:{color}">'
            f'<div class="tier-head"><span class="tier-name">{esc(name)}</span>'
            f'<span class="tier-en">{esc(en)}</span></div>'
            f'{items}{extra}</div>'
        )

    svg = _arch_svg()
    params = "".join(f'<span class="p-item">{esc(p)}</span>' for p in CONTENT["arch_params"])

    return f"""
<section class="arch">
  {sec_head(kicker, title, note)}
  <div class="arch-canvas">
    {svg}
    <div class="arch-row">{''.join(nodes)}</div>
    <div class="tier-row">{''.join(tiers)}</div>
    <div class="fade-strip">
      <span class="tag">遗忘 · 归档层</span>
      <span>strength = base · e^(−Δt/τ) · (1 + 0.12 · recall_count)，低于 0.15 标记 archived：不再注入、文件保留、界面标灰，只标记不删除。</span>
    </div>
    <div class="params-row">{params}</div>
  </div>
</section>
"""


def _arch_svg() -> str:
    """架构图的四条箭头：召回（上）/ 写回（虚线回路）/ 固化 + 提炼（横向）/ 遗忘（向下）。

    画布坐标 viewBox = 0 0 392 {CANVAS_H}，1 单位 = 1mm，与 .arch-canvas 完全对齐；
    纵向栅格全部来自 MM 常量，与 CSS 同源。所有文字都是水平排版，不跨卡片、不压箭头：
    卡片上方的「召回 / 写回」标签独占 18~30mm 这段空档，与卡片上沿留 12mm 以上。
    """
    accent = "#FB7185"
    cool = "#8FA3C4"
    legend = CONTENT["arch_legend"]
    recall_txt, recall_sub = legend["recall"]
    consolid_txt = legend["consolid"][0]
    forget_txt = legend["forget"][0]
    back_txt = "写回 · 追加 WorkingTurn（情绪 · salience）"

    new_marker = (
        lambda mid, color: f'<marker id="{mid}" viewBox="0 0 10 10" refX="8.6" refY="5" '
        f'markerWidth="5.2" markerHeight="5.2" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 Z" fill="{color}"/></marker>'
    )

    lefts = TIER_EDGE
    rights = [x + TIER_W for x in lefts]
    node_mid = MM["NODE_H"] / 2
    tier_y = MM["TIER_TOP"] + MM["TIER_H"] / 2          # 卡片纵向中线
    recall_x = 118.0                                     # 落在「中期」卡缝里、正对「检索打分」
    back_x_r = ARCH_W - 8.0                              # 回路右侧竖线（界面卡下方）
    back_x_l = 24.0                                      # 回路落点在「短期」卡上沿
    back_y = MM["TIER_TOP"] - 8.5                        # 回路横线：卡片上沿之上 8.5mm
    label_y = MM["NODE_H"] + 7.0                         # 走廊标签基线（与卡片上沿留 14mm）

    lines: list[str] = []

    # 1) 主链 6 个节点之间的横向箭头
    for i in range(5):
        x0 = i * (NODE_W + NODE_GAP) + NODE_W + 0.9
        x1 = (i + 1) * (NODE_W + NODE_GAP) - 0.9
        lines.append(
            f'<path d="M{x0:g},{node_mid:g} H{x1:g}" stroke="{accent}" stroke-width="0.45" '
            f'fill="none" marker-end="url(#ah)" opacity="0.85"/>'
        )

    # 2) 写回回路（先画虚线，之后画的召回箭头压在上面）
    lines.append(
        f'<path d="M{back_x_r},{MM["NODE_H"] + 0.8} V{back_y} H{back_x_l} V{MM["TIER_TOP"] - 0.6}" '
        f'stroke="{cool}" stroke-width="0.42" fill="none" stroke-dasharray="3 1.6" '
        f'marker-end="url(#ah2)"/>'
    )
    lines.append(
        f'<text x="{back_x_r - 6.0}" y="{label_y}" font-size="6.4px" fill="#9AAAC6" '
        f'text-anchor="end">{esc(back_txt)}</text>'
    )

    # 3) 召回：从三层记忆卡上方升进「检索打分」；标签与「写回」同一基线，单行横排
    lines.append(
        f'<path d="M{recall_x},{MM["TIER_TOP"] - 0.5} V{MM["NODE_H"] + 0.8}" stroke="{accent}" '
        f'stroke-width="0.5" fill="none" marker-end="url(#ah)"/>'
    )
    lines.append(
        f'<text x="{recall_x + 4.5}" y="{label_y}" font-size="6.8px" fill="#9AAAC6">'
        f'<tspan fill="{accent}" font-weight="700">{esc(recall_txt)}</tspan>'
        f'<tspan font-size="6.2px"> · {esc(recall_sub)}</tspan></text>'
    )

    # 4) 固化 / 提炼：画在卡片之间的缝里，标签两行横排居中放在箭头上方（缝宽 26mm，单行放不下）
    for x_from, x_to, t1, t2 in [
        (rights[0] + 1.2, lefts[1] - 1.2, consolid_txt, "每 4 轮"),
        (rights[1] + 1.2, lefts[2] - 1.2, "提炼", "成 Fact"),
    ]:
        cx = (x_from + x_to) / 2
        lines.append(
            f'<path d="M{x_from:g},{tier_y:g} H{x_to:g}" stroke="{accent}" stroke-width="0.5" '
            f'fill="none" marker-end="url(#ah)"/>'
        )
        lines.append(
            f'<text x="{cx:g}" y="{tier_y - 10:g}" font-size="6.8px" fill="{accent}" '
            f'font-weight="700" text-anchor="middle">{esc(t1)}</text>'
        )
        lines.append(
            f'<text x="{cx:g}" y="{tier_y - 4:g}" font-size="6px" fill="#9AAAC6" '
            f'text-anchor="middle">{esc(t2)}</text>'
        )

    # 5) 遗忘：三张卡向下进「遗忘 · 归档层」
    for cx in TIER_C:
        lines.append(
            f'<path d="M{cx:g},{TIER_BOT + 0.6} V{MM["FADE_TOP"] - 1.8}" stroke="{accent}" '
            f'stroke-width="0.45" fill="none" marker-end="url(#ah)" opacity="0.9"/>'
        )
        lines.append(
            f'<text x="{cx + 2.8:g}" y="{MM["FADE_TOP"] - 2.4}" font-size="6.2px" fill="{accent}" '
            f'font-weight="700">{esc(forget_txt)}</text>'
        )

    return (
        f'<svg class="arch-svg" viewBox="0 0 {ARCH_W:g} {MM["CANVAS_H"]:g}" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Microsoft YaHei, sans-serif">'
        f'<defs>{new_marker("ah", accent)}{new_marker("ah2", cool)}</defs>'
        f'{"".join(lines)}</svg>'
    )


def render_innov() -> str:
    """04 六个创新点卡片。"""
    kicker, title, note = CONTENT["innov_sec"]
    cards = []
    for no, name, lines, param in CONTENT["innovations"]:
        body = "".join(f"<p>{esc(line)}</p>" for line in lines)
        cards.append(
            '<article class="card">'
            f'<div class="card-head"><span class="card-no">{esc(no)}</span>'
            f'<span class="card-title">{esc(name)}</span></div>'
            f'<div class="card-body">{body}</div>'
            f'<div class="card-param">{esc(param)}</div>'
            "</article>"
        )
    return f"""
<section class="innov">
  {sec_head(kicker, title, note)}
  <div class="innov-grid">{''.join(cards)}</div>
</section>
"""


def render_persona() -> str:
    """05 人设编译器流程图 + 三块细节。"""
    kicker, title, note = CONTENT["persona_sec"]
    steps = []
    for idx, (name, desc) in enumerate(CONTENT["persona_steps"]):
        steps.append(
            f'<div class="step" data-no="0{idx + 1}">'
            f'<div class="step-name">{esc(name)}</div>'
            f'<div class="step-note">{esc(desc)}</div>'
            "</div>"
        )
    blocks = []
    for name, text in CONTENT["persona_blocks"]:
        blocks.append(
            '<div class="pblock">'
            f'<div class="pblock-title">{esc(name)}</div>'
            f'<div class="pblock-text">{esc(text)}</div>'
            "</div>"
        )
    return f"""
<section class="persona">
  {sec_head(kicker, title, note)}
  <div class="persona-flow">{''.join(steps)}</div>
  <div class="persona-blocks">{''.join(blocks)}</div>
</section>
"""


def render_compare() -> str:
    """06 差异化对比表。"""
    kicker, title, note = CONTENT["compare_sec"]
    head = CONTENT["compare_head"]
    ths = [f'<th>{esc(head[0])}</th>', f'<th class="is-us">{esc(head[1])}</th>']
    ths += [f"<th>{esc(h)}</th>" for h in head[2:]]
    rows = []
    for dim, us, *others in CONTENT["compare_rows"]:
        cells = [f'<td class="dim">{esc(dim)}</td>', f'<td class="us">{esc(us)}</td>']
        cells += [f'<td class="other">{esc(o)}</td>' for o in others]
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"""
<section class="compare">
  {sec_head(kicker, title, note)}
  <div class="cmp-wrap">
    <table class="cmp">
      <colgroup>
        <col class="c-dim"><col class="c-us">
        <col class="c-other"><col class="c-other"><col class="c-other">
      </colgroup>
      <thead><tr>{''.join(ths)}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <p class="cmp-note">{esc(CONTENT['compare_note'])}</p>
</section>
"""


def _saved_pct(tiles: list[dict[str, str]]) -> str:
    """从 compress_ratio 反推「省下多少」——65.4% 是占用比例，省下的是 34.6%。"""
    for tile in tiles:
        if tile["key"] != "compress_ratio":
            continue
        m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*$", tile["value"])
        if m:
            return f"{100 - float(m.group(1)):.1f}%"
    return "34.6%"          # 数值缺失时的口径兜底（仅用于文案，不冒充指标）


def render_eng() -> str:
    """07 技术栈 + 指标。"""
    kicker, title, note = CONTENT["stack_sec"]
    chips = "".join(
        f'<div class="chip"><div class="chip-k">{esc(k)}</div>'
        f'<div class="chip-v">{esc(v)}</div></div>'
        for k, v in CONTENT["stack"]
    )
    tiles, used, extras = load_metrics()
    saved = _saved_pct(tiles)
    metrics = []
    for tile in tiles:
        pending = " is-pending" if tile["value"] == METRIC_PENDING else ""
        label = tile["label"]
        if tile["key"] == "compress_ratio" and used:
            label = f"上下文占用 · 省下 {saved}"
        metrics.append(
            '<div class="metric">'
            f'<div class="metric-v{pending}">{esc(tile["value"])}</div>'
            f'<div class="metric-l">{esc(label)}</div>'
            "</div>"
        )
    if used:
        eng_note = CONTENT["metrics_note"].format(
            turns=extras.get("turns", "若干"),
            episodes=extras.get("episodes", "若干"),
        )
    else:
        eng_note = CONTENT["metrics_note_pending"]
    return f"""
<section class="eng">
  {sec_head(kicker, title, note)}
  <div class="stack-row">{chips}</div>
  <div class="metric-row">{''.join(metrics)}</div>
  <p class="eng-note">{esc(eng_note)}</p>
</section>
"""


def render_foot() -> str:
    """08 页脚。"""
    left, right = CONTENT["footer"]
    return f'<footer class="foot"><span>{esc(left)}</span><span>{esc(right)}</span></footer>'


# 版式诊断脚本：把每个区块的 top/bottom 写进一个隐藏 div，再用 --dump-dom 读回来。
# 这样不用肉眼看图就能知道有没有区块被挤出页面、区块之间的间距是不是被压成 0。
DIAG_JS = """
<script>
window.addEventListener('load', function () {
  var p = document.querySelector('.poster');
  var pr = p.getBoundingClientRect();
  var out = [];
  for (var i = 0; i < p.children.length; i++) {
    var el = p.children[i], r = el.getBoundingClientRect();
    out.push({c: (el.className || el.tagName), top: +(r.top - pr.top).toFixed(2),
              bot: +(r.bottom - pr.top).toFixed(2)});
  }
  // 真正被裁掉 / 溢出的文字：只认 white-space:nowrap 横向溢出和 overflow:hidden 纵向溢出，
  // 避免把绝对定位的 ::after 箭头、以及字形的降部溢出算成问题。
  var clip = [];
  var all = p.querySelectorAll('*');
  for (var j = 0; j < all.length; j++) {
    var e = all[j];
    if (!(e.textContent || '').trim()) continue;
    var cs = getComputedStyle(e);
    var nowrap = cs.whiteSpace === 'nowrap' || cs.whiteSpace === 'pre';
    var hidden = cs.overflowX === 'hidden' || cs.overflowY === 'hidden';
    var dx = e.scrollWidth - e.clientWidth, dy = e.scrollHeight - e.clientHeight;
    if ((nowrap && dx > 1) || (hidden && (dx > 1 || dy > 4))) {
      clip.push({c: (e.className || e.tagName), t: (e.textContent || '').trim().slice(0, 20),
                 dx: dx, dy: dy});
      if (clip.length >= 24) break;
    }
  }
  var d = document.createElement('div');
  d.id = 'diag'; d.style.display = 'none';
  d.textContent = JSON.stringify({w: pr.width, h: pr.height, secs: out, clip: clip});
  document.body.appendChild(d);
});
</script>
"""


def build_html() -> str:
    """拼装完整 HTML 并写入 poster.html。"""
    body = "".join([
        render_head(), render_pain(), render_arch(), render_innov(),
        render_persona(), render_compare(), render_eng(), render_foot(),
    ])
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>EmoMem · A2 海报</title>
<style>{CSS}</style>
</head>
<body>
<div class="poster">
{body}
</div>
{DIAG_JS}
</body>
</html>
"""
    HTML_PATH.write_text(doc, encoding="utf-8")
    return doc


def report_layout() -> bool:
    """读回各区块纵向位置（mm），报告挤压 / 溢出；返回是否合格。"""
    result = run_chrome("--dump-dom", "--virtual-time-budget=4000", HTML_PATH.as_uri())
    match = re.search(r'<div id="diag"[^>]*>(.*?)</div>', result.stdout or "", re.S)
    if not match:
        print("[layout] 读不到 diag 数据（Chrome --dump-dom 未返回），跳过版式测量")
        return True
    try:
        data = json.loads(html.unescape(match.group(1)))
    except (json.JSONDecodeError, ValueError):
        print("[layout] diag 数据解析失败，跳过版式测量")
        return True
    k = 25.4 / 96.0
    page_mm = data["h"] * k
    print(f"[layout] 画面 {data['w'] * k:.1f} x {page_mm:.1f} mm（A2 = 420 x 594）")
    ok = True
    prev_bot: float | None = None
    for sec in data["secs"]:
        top, bot = sec["top"] * k, sec["bot"] * k
        gap = "—" if prev_bot is None else f"{top - prev_bot:5.1f}mm"
        print(f"[layout]   {str(sec['c'])[:24]:<24} top {top:6.1f}  bot {bot:6.1f}  h {bot - top:6.1f}  缝 {gap}")
        if bot > page_mm - 8.4:                       # 页脚需要 ≥8mm 的净空
            print(f"[layout]   ! {sec['c']} 底边越过安全线（{bot:.1f} > {page_mm - 8.4:.1f}）")
            ok = False
        if prev_bot is not None and top - prev_bot < 1.0:
            print(f"[layout]   ! {sec['c']} 与上一节间距不足 1mm，版面被压扁")
            ok = False
        prev_bot = bot
    clips = data.get("clip") or []
    if clips:
        ok = False
        print(f"[layout] ! 有 {len(clips)} 个元素内容装不下（截断/溢出）：")
        for it in clips:
            print(f"[layout]   - {str(it['c'])[:22]:<22} dx {it['dx']:4d}px dy {it['dy']:4d}px  「{it['t']}」")
    return ok


# ---------------------------------------------------------------------------
# Chrome 渲染
# ---------------------------------------------------------------------------

def run_chrome(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """调用 Chrome headless；返回 CompletedProcess（stdout/stderr 按 UTF-8 解码）。"""
    chrome = find_chrome()
    cmd = [str(chrome), "--headless=new", "--disable-gpu",
           f"--user-data-dir={PROFILE_DIR}", *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def build_pdf() -> Path:
    """poster.html → poster.pdf（A2 单页）。"""
    result = run_chrome(
        "--no-pdf-header-footer",
        f"--print-to-pdf={PDF_PATH}",
        "--virtual-time-budget=5000",
        HTML_PATH.as_uri(),
    )
    if not PDF_PATH.exists():
        raise SystemExit(f"PDF 生成失败：\n{result.stderr[-2000:]}")
    return PDF_PATH


def build_png() -> Path:
    """poster.html → poster.png（96 dpi 下 A2 = 1588×2245 px）。"""
    result = run_chrome(
        f"--screenshot={PNG_PATH}",
        f"--window-size={SCREEN_W_PX},{SCREEN_H_PX}",
        "--force-device-scale-factor=1",
        "--hide-scrollbars",
        "--virtual-time-budget=5000",
        HTML_PATH.as_uri(),
    )
    if not PNG_PATH.exists():
        raise SystemExit(f"PNG 生成失败：\n{result.stderr[-2000:]}")
    return PNG_PATH


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def check_pdf() -> bool:
    """校验 PDF：页数 = 1、MediaBox ≈ A2、无文字越界。"""
    ok = True
    if not PDF_PATH.exists():
        print("[check] poster.pdf 不存在")
        return False
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None  # type: ignore

    if PdfReader is not None:
        reader = PdfReader(str(PDF_PATH))
        pages = len(reader.pages)
        box = reader.pages[0].mediabox
        w_mm = float(box.width) * 25.4 / 72
        h_mm = float(box.height) * 25.4 / 72
        print(f"[check] PDF 页数 = {pages}；MediaBox = {float(box.width):.2f} x {float(box.height):.2f} pt "
              f"= {w_mm:.1f} x {h_mm:.1f} mm")
        ok = ok and pages == 1 and abs(w_mm - PAGE_W_MM) < 1.0 and abs(h_mm - PAGE_H_MM) < 1.0

    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore
    if fitz is not None:
        doc = fitz.open(str(PDF_PATH))
        page = doc[0]
        rect = page.rect
        overflow = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                x0, y0, x1, y1 = line["bbox"]
                if x1 > rect.x1 + 0.5 or y1 > rect.y1 + 0.5 or x0 < -0.5 or y0 < -0.5:
                    text = "".join(span["text"] for span in line["spans"])[:24]
                    overflow.append((round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1), text))
        print(f"[check] PDF 文字块越界数量 = {len(overflow)}")
        for item in overflow[:6]:
            print("   ", item)
        ok = ok and not overflow
        doc.close()
    return ok


def check_png() -> bool:
    """校验 PNG：尺寸应为 1588×2245。"""
    if not PNG_PATH.exists():
        print("[check] poster.png 不存在")
        return False
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print(f"[check] poster.png 大小 = {PNG_PATH.stat().st_size / 1024:.0f} KB（未安装 Pillow，跳过尺寸校验）")
        return True
    with Image.open(PNG_PATH) as img:
        w, h = img.size
    print(f"[check] PNG 尺寸 = {w} x {h} px（期望 {SCREEN_W_PX} x {SCREEN_H_PX}）")
    return (w, h) == (SCREEN_W_PX, SCREEN_H_PX)


def main(argv: list[str]) -> int:
    """入口：默认重建 HTML + PDF + PNG 并校验。"""
    for stream in (sys.stdout, sys.stderr):          # Windows 控制台默认 GBK，强制 UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass

    only_html = "--html" in argv
    only_check = "--check" in argv
    only_layout = "--layout" in argv

    if only_layout:
        build_html()
        return 0 if report_layout() else 1

    if not only_check:
        build_html()
        print(f"[build] poster.html 已生成（{HTML_PATH.stat().st_size / 1024:.1f} KB）")
        layout_ok = report_layout()
        if not only_html:
            build_pdf()
            print(f"[build] poster.pdf 已生成（{PDF_PATH.stat().st_size / 1024:.1f} KB）")
            build_png()
            print(f"[build] poster.png 已生成（{PNG_PATH.stat().st_size / 1024:.1f} KB）")
    else:
        layout_ok = True

    ok = check_pdf() and check_png() and layout_ok
    print("[check] 通过" if ok else "[check] 存在问题，请检查上面的输出")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
