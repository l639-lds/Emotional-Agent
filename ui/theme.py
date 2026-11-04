"""界面主题：同一套版式、两套配色（浅色 / 深色），只在 app.py 里注入一次。

设计约束（来自对 taste-skill / impeccable 两个设计规则仓库的研究）：

* 一个 ground、一个 ink、**一个 accent**；accent 只出现在主按钮、当前导航项和
  极少数状态点上（占屏 < 5%）。浅色下 accent 压暗一档保证对比度。
* 层次只靠 tone step 与间距，不用 1px 分隔线、不用静置阴影、不用渐变、不用玻璃拟态。
* 字号只有 5 档（13 / 16 / 20 / 30 / 40），正文 16px、行高 1.75。
* 界面里不出现 emoji 当图标（头像是「内容」，仍然保留）。
* 圆角分档：结构块 2px、控件 6px、卡片 12px、气泡 16px（说话那侧收成 5px）。

Streamlit 的组件内部 DOM 会随版本变化，所以这里只覆盖稳定的 ``data-testid``；
改完必须真机截图复查。配色由 :func:`use` 决定，两套都写在本文件里，别处不许写死 hex。
"""

from __future__ import annotations

import streamlit as st

# ---------- 两套配色：改这里就等于改全站 ----------
LIGHT: dict[str, str] = {
    "BG": "#F3F0E8",          # 暖白纸感 ground（比纯白低一点，白气泡才浮得起来）
    "SURFACE_1": "#FFFFFF",   # 卡片 / 侧边栏 / 助手气泡
    "SURFACE_2": "#ECE7DA",   # 输入框 / 斑马纹
    "SURFACE_3": "#E1DAC9",   # 次级按钮底
    "SURFACE_4": "#CFC3A8",   # 次级按钮 hover
    "INK": "#1A1F27",
    "MUTED": "#5B6472",       # 对 ground 约 6.2:1
    "FAINT": "#8B9199",
    "ACCENT": "#B26B24",      # 白底上 4.6:1
    "ACCENT_INK": "#FFFFFF",
    "ACCENT_SOFT": "rgba(178, 107, 36, 0.16)",
    "ACCENT_LINE": "rgba(178, 107, 36, 0.44)",
    "BUBBLE_LINE": "#C8BDA4",
    "BUBBLE_SHADOW": "0 1px 2px rgba(26, 31, 39, 0.07)",
}

DARK: dict[str, str] = {
    "BG": "#0F141B",
    "SURFACE_1": "#151C25",
    "SURFACE_2": "#1B2430",
    "SURFACE_3": "#222D3B",
    "SURFACE_4": "#2B3748",
    "INK": "#E7ECF4",
    "MUTED": "#93A0B4",
    "FAINT": "#6B7789",
    "ACCENT": "#E0A458",
    "ACCENT_INK": "#12100C",
    "ACCENT_SOFT": "rgba(224, 164, 88, 0.16)",
    "ACCENT_LINE": "rgba(224, 164, 88, 0.34)",
    "BUBBLE_LINE": "#1E2836",
    "BUBBLE_SHADOW": "none",
}

MODES = ("浅色", "深色")
DEFAULT_MODE = "浅色"
MODE_KEY = "appearance"

# 下面这些名字是「当前生效的一套」，由 use() 覆盖；别的模块一律 theme.XXX 取值。
BG = SURFACE_1 = SURFACE_2 = SURFACE_3 = SURFACE_4 = ""
INK = MUTED = FAINT = ACCENT = ACCENT_INK = ACCENT_SOFT = ACCENT_LINE = ""
BUBBLE_LINE = ""
BUBBLE_SHADOW = ""


FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", '
    '"Noto Sans SC", sans-serif'
)

def _build_css() -> str:
    """按「当前生效的一套」拼 CSS；``use()`` 之后重算一次。"""
    return f"""
<style>
:root {{
  --bg: {BG};
  --surface-1: {SURFACE_1};
  --surface-2: {SURFACE_2};
  --surface-3: {SURFACE_3};
  --surface-4: {SURFACE_4};
  --ink: {INK};
  --muted: {MUTED};
  --faint: {FAINT};
  --accent: {ACCENT};
  --accent-ink: {ACCENT_INK};
  --accent-soft: {ACCENT_SOFT};
  --accent-line: {ACCENT_LINE};
  --bubble-line: {BUBBLE_LINE};
  --bubble-shadow: {BUBBLE_SHADOW};
  --radius: 2px;            /* 结构块：表格 / 斑马纹行，棱角是它的性格 */
  --radius-control: 6px;    /* 控件：按钮 / 输入框 / pills */
  --radius-card: 12px;      /* 卡片：折叠面板 / 提示 / 建议块 */
  --radius-bubble: 16px;    /* 气泡：说话的那一侧收成 --radius-tail */
  --radius-tail: 5px;
  --step-small: 13px;
  --step-body: 16px;
  --step-lead: 20px;
  --step-head: 30px;
  --step-display: 40px;
  --ease: cubic-bezier(0.22, 1, 0.36, 1);
}}

/* ---------- 全局 ---------- */
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {{
  font-family: {FONT_STACK};
  color: var(--ink);
  background: var(--bg);
}}
[data-testid="stAppViewContainer"] > .main {{ background: var(--bg); }}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], #MainMenu, footer, [data-testid="stAppDeployButton"] {{
  display: none !important;
}}
.block-container {{
  max-width: 1120px;
  padding: 34px 40px 96px 40px;
}}
[data-testid="stVerticalBlock"] {{ gap: 0.7rem; }}
::selection {{ background: var(--accent); color: var(--accent-ink); }}
* {{ scrollbar-color: var(--surface-3) transparent; scrollbar-width: thin; }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* ---------- 排版 ---------- */
h1, h2, h3, h4, h5 {{ color: var(--ink); font-weight: 600; letter-spacing: -0.01em; }}
[data-testid="stMarkdownContainer"] h1 {{ font-size: var(--step-display); line-height: 1.15; }}
[data-testid="stMarkdownContainer"] h2 {{ font-size: var(--step-head); line-height: 1.3; }}
[data-testid="stMarkdownContainer"] h3 {{ font-size: var(--step-lead); line-height: 1.45; }}
[data-testid="stMarkdownContainer"] h4 {{ font-size: var(--step-body); }}
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {{
  font-size: var(--step-body); line-height: 1.75; color: var(--ink);
}}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
  font-size: var(--step-small); color: var(--muted); line-height: 1.6;
}}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; text-underline-offset: 3px; }}
code {{ background: var(--surface-2); color: var(--ink); border-radius: var(--radius); }}
hr {{ border: none; border-top: 1px solid var(--surface-2); }}

/* ---------- 侧边栏：只留 wordmark + 导航 ---------- */
[data-testid="stSidebar"] {{ background: var(--surface-1); }}
[data-testid="stSidebar"] > div:first-child {{ padding: 26px 20px 60px 20px; }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.45rem; }}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{
  font-size: var(--step-small); color: var(--muted); letter-spacing: 0;
}}
.emo-brand {{
  font-size: var(--step-lead); font-weight: 600; letter-spacing: -0.02em; color: var(--ink);
  margin: 2px 0 2px 2px;
}}
.emo-brand span {{ color: var(--accent); }}
.emo-tagline {{
  font-size: var(--step-small); color: var(--muted); margin: 0 0 22px 2px; line-height: 1.6;
}}

/* 视图导航：把 radio 做成「一列按钮」（隐藏圆点，选中项实心 accent） */
[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 6px; }}
[data-testid="stSidebar"] div[role="radiogroup"] > label {{
  padding: 10px 14px; border-radius: var(--radius-control); cursor: pointer;
  border: 1px solid var(--surface-4); background: var(--surface-1);
  transition: background 150ms var(--ease), border-color 150ms var(--ease),
    color 150ms var(--ease);
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
  background: var(--surface-3); border-color: var(--surface-4);
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {{ display: none; }}
[data-testid="stSidebar"] div[role="radiogroup"] > label p {{
  font-size: var(--step-body); color: var(--ink); margin: 0;
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--accent); border-color: var(--accent);
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--accent-ink); font-weight: 600;
}}

/* 外观开关是「两个小 chip」，别跟着导航按钮变高 */
[data-testid="stSidebar"] .st-key-appearance div[role="radiogroup"] {{ gap: 8px; }}
[data-testid="stSidebar"] .st-key-appearance div[role="radiogroup"] > label {{
  padding: 6px 14px; background: transparent; border-color: var(--surface-4);
}}
[data-testid="stSidebar"] .st-key-appearance div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--surface-3); border-color: var(--surface-3);
}}
[data-testid="stSidebar"] .st-key-appearance div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--ink); font-weight: 500;
}}

/* ---------- 按钮：一个主按钮，其余是 tone step ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
  border: 1px solid var(--surface-4); border-radius: var(--radius-control); font-family: {FONT_STACK};
  font-size: var(--step-body); font-weight: 500; padding: 9px 18px; min-height: 38px;
  background: var(--surface-3); color: var(--ink);
  transition: background 150ms var(--ease), border-color 150ms var(--ease),
    color 150ms var(--ease), transform 120ms var(--ease);
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  background: var(--surface-4); border-color: var(--surface-4); color: var(--ink);
}}
.stButton > button:active {{ transform: translateY(1px); }}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] {{
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 600;
}}
.stButton > button[kind="primary"]:hover {{ background: var(--accent); border-color: var(--accent); color: var(--accent-ink); filter: brightness(1.06); }}
.stButton > button:disabled {{ background: var(--surface-1); border-color: var(--surface-1); color: var(--faint); }}

/* ---------- 输入类控件 ---------- */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {{
  background: var(--surface-1) !important; border: 1px solid var(--surface-2) !important;
  border-radius: var(--radius-control) !important; color: var(--ink) !important;
}}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea {{
  color: var(--ink) !important; font-size: var(--step-body); font-family: {FONT_STACK};
}}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {{
  border-color: var(--accent) !important;
}}
/* 下拉框：BaseWeb 的 select 有三层壳，只上最内层会让四角露出主题底色 */
[data-testid="stSelectbox"] div[data-baseweb="select"] {{ background: transparent !important; }}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
  background: var(--surface-1) !important; border: 1px solid var(--surface-3) !important;
  border-radius: var(--radius-control) !important; box-shadow: none !important;
}}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {{ border-color: var(--surface-4) !important; }}
[data-testid="stSelectbox"] div[data-baseweb="select"] div[role="button"],
[data-testid="stSelectbox"] div[data-baseweb="select"] div[role="combobox"] {{
  background: var(--surface-1) !important; border: none !important;
}}
[data-testid="stSelectbox"] div[data-baseweb="select"] svg {{ color: var(--muted) !important; fill: var(--muted) !important; }}
/* 兜底：万一进程是用旧的深色主题配置起的，BaseWeb 会给下拉框塞深色底，
   这里把所有子层的底色一起清掉，只留最外层那一块我们自己的 surface-1。 */
[data-testid="stSelectbox"] div[data-baseweb="select"] div {{
  background-color: transparent !important; box-shadow: none !important;
}}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child {{
  background: var(--surface-1) !important; border: 1px solid var(--surface-3) !important;
  border-radius: var(--radius-control) !important;
}}

[data-baseweb="menu"] {{ background: var(--surface-1) !important; border: 1px solid var(--surface-3) !important;
  border-radius: var(--radius-control) !important; }}
[data-baseweb="menu"] li {{ color: var(--ink) !important; }}
[data-baseweb="menu"] li:hover {{ background: var(--surface-2) !important; }}

/* 侧边栏里外观开关与上面的控件之间留一口气 */
[data-testid="stSidebar"] .st-key-appearance {{ margin-top: 10px; }}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] {{ padding: 0 14px 12px 14px; }}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] > div {{ padding-top: 6px; }}

/* 对话输入框里的 textarea 也带 data-baseweb，别让它套上强调色边框 */
[data-testid="stChatInput"] [data-baseweb="textarea"]:focus-within {{
  border-color: var(--surface-3) !important;
}}
/* Streamlit 自己的 wrapper 在 focus 时会给内层 div 上 primaryColor 边框，一并压掉 */
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div {{
  border-color: var(--surface-2) !important;
}}
[data-testid="stChatInput"]:focus-within > div,
[data-testid="stChatInput"]:focus-within > div > div {{
  border-color: var(--surface-3) !important;
}}
[data-baseweb="popover"] li {{ font-size: var(--step-body); }}

/* Streamlit 原生输入框的底色来自 config.toml 那一套，切配色时得强制跟 token 走 */
[data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input, [data-baseweb="base-input"],
[data-baseweb="textarea"], [data-baseweb="input"] {{
  background: var(--surface-1) !important; color: var(--ink) !important;
  border-color: var(--surface-3) !important;
}}
[data-testid="stTextArea"] > div, [data-testid="stTextInput"] > div {{
  background: transparent !important; box-shadow: none !important;
}}
[data-testid="stTextArea"] textarea:focus, [data-testid="stTextInput"] input:focus {{
  border-color: var(--accent) !important; box-shadow: none !important;
}}
::placeholder {{ color: var(--faint) !important; opacity: 1; }}

/* ---------- 对话输入框 ---------- */
[data-testid="stChatInput"] {{
  background: var(--surface-1); border: 1px solid var(--surface-2);
  border-radius: var(--radius-card); padding: 4px 6px;
}}
/* 底部固定输入区的外壳由 Streamlit 主题上色，切外观时要压回 token */
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"],
[data-testid="stBottomContainer"], [data-testid="stAppViewBlockContainer"] {{
  background: var(--bg) !important;
}}
[data-testid="stChatInput"] > div, [data-testid="stChatInput"] > div > div,
[data-testid="stChatInput"] div[data-baseweb="textarea"],
[data-testid="stChatInput"] div[data-baseweb="base-input"] {{
  background: var(--surface-1) !important; border-color: var(--surface-2) !important;
}}
[data-testid="stChatInput"] textarea {{
  background: transparent !important; font-size: var(--step-body); color: var(--ink) !important;
}}
[data-testid="stChatInput"] textarea {{ font-size: var(--step-body); color: var(--ink); }}
[data-testid="stChatInput"]:focus-within {{ border-color: var(--surface-3); }}
/* 输入区自己的 focus 由容器边框提示，不要再套一层粗 outline */
[data-testid="stChatInput"] textarea:focus-visible,
[data-testid="stChatInput"] textarea:focus {{ outline: none !important; box-shadow: none !important; }}
[data-testid="stChatInput"] button {{ background: transparent !important; color: var(--muted) !important; }}
[data-testid="stChatInput"] button:hover {{ color: var(--accent) !important; }}

/* ---------- 原生弹层 / 代码块：两套配色都要跟得上（config.toml 只能写一套） ---------- */
[data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="popover"] ul, [role="listbox"] {{
  background: var(--surface-1) !important; border-radius: var(--radius-card) !important;
}}
[data-baseweb="popover"] li, [role="option"] {{ background: transparent !important; color: var(--ink) !important; }}
[data-baseweb="popover"] li:hover, [role="option"]:hover {{ background: var(--surface-2) !important; }}
[data-testid="stCode"], pre {{
  background: var(--surface-1) !important; color: var(--ink) !important;
  border-radius: var(--radius-card) !important;
}}

/* ---------- tab：文字 + 细强调条，无底线、无圆角胶囊 ---------- */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  gap: 26px; border-bottom: none; background: transparent;
}}
[data-testid="stTabs"] [data-baseweb="tab"] {{
  background: transparent; padding: 2px 0 10px 0;
}}
[data-testid="stTabs"] [data-baseweb="tab"] p {{
  font-size: var(--step-body); color: var(--muted); margin: 0;
}}
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] p {{ color: var(--ink); font-weight: 600; }}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background: var(--accent); height: 2px; }}

/* ---------- 折叠面板 / 提示 / 表格 ---------- */
[data-testid="stExpander"] {{ background: var(--surface-1); border-radius: var(--radius-card); }}
[data-testid="stExpander"] details {{ border: none !important; background: transparent; }}
[data-testid="stExpander"] summary {{ font-size: var(--step-body); color: var(--ink); padding: 10px 14px; }}
[data-testid="stExpander"] summary:hover {{ background: var(--surface-2); }}
[data-testid="stExpander"] p {{ font-size: var(--step-small); color: var(--muted); }}
[data-testid="stAlert"] {{ border-radius: var(--radius-card); border: none; background: var(--surface-1); }}
[data-testid="stAlert"] p {{ font-size: var(--step-small); color: var(--ink); }}
[data-testid="stDataFrame"] {{ border-radius: var(--radius); }}
[data-testid="stFileUploaderDropzone"] {{
  background: var(--surface-1); border: 1px dashed var(--surface-3); border-radius: var(--radius-card);
}}
[data-testid="stFileUploaderDropzone"] p, [data-testid="stFileUploaderDropzone"] span {{
  font-size: var(--step-small) !important; color: var(--muted) !important;
}}
[data-testid="stPills"] button {{
  border-radius: var(--radius-control) !important; background: var(--surface-2) !important;
  border: none !important; color: var(--ink) !important; font-size: var(--step-body);
}}
[data-testid="stPills"] button[aria-checked="true"] {{
  background: var(--accent) !important; color: var(--accent-ink) !important;
}}
/* 进度条只当刻度用：2px 细线 + 限宽（只压 baseweb 那层，别碰到文字） */
[data-testid="stProgress"] {{ padding: 0 !important; max-width: 360px; }}
[data-testid="stProgress"] [data-baseweb="progress-bar"] {{ height: 2px !important; }}
[data-testid="stProgress"] [data-baseweb="progress-bar"] > div {{ height: 2px !important; border-radius: 0 !important;
  background: var(--surface-3) !important; }}
[data-testid="stProgress"] [data-baseweb="progress-bar"] > div > div {{ background: var(--accent) !important;
  height: 2px !important; border-radius: 0 !important; }}
[data-testid="stProgress"] p {{ font-size: var(--step-small); color: var(--muted); margin: 0 0 8px; }}
[data-testid="stMetricValue"] {{ font-size: var(--step-head); }}
[data-testid="stToggle"] p, [data-testid="stCheckbox"] p {{ font-size: var(--step-body); }}

/* ---------- 页头 ---------- */
.emo-head {{ display: flex; align-items: flex-end; justify-content: space-between;
  gap: 24px; margin: 2px 0 18px; }}
.emo-head-name {{ font-size: var(--step-head); font-weight: 600; letter-spacing: -0.015em;
  line-height: 1.2; color: var(--ink); }}
.emo-head-name .emo-who {{ font-size: var(--step-body); color: var(--muted); font-weight: 400;
  margin-left: 10px; letter-spacing: 0; }}
.emo-head-meta {{ font-size: var(--step-small); color: var(--muted); margin-top: 6px; }}
.emo-head-meta b {{ color: var(--ink); font-weight: 500; }}
.emo-head-right {{ font-size: var(--step-small); color: var(--faint); text-align: right;
  white-space: nowrap; padding-bottom: 3px; }}

/* 五层总览：自己画行，不用 st.dataframe（canvas 表格抢眼且不可控） */
.emo-layers {{ margin: 2px 0 14px; }}
.emo-layer {{ display: grid; grid-template-columns: 48px 110px 92px 1fr; align-items: baseline;
  gap: 14px; padding: 11px 14px; }}
.emo-layer:nth-child(even) {{ background: var(--surface-1); }}
.emo-layer-head {{ font-size: var(--step-small); color: var(--faint); padding-top: 0; }}
.emo-layer-code {{ font-size: var(--step-small); color: var(--muted); letter-spacing: 0.02em; }}
.emo-layer-name {{ font-size: var(--step-body); color: var(--ink); }}
.emo-layer-count {{ font-size: var(--step-body); color: var(--ink); font-variant-numeric: tabular-nums; }}
.emo-layer-policy {{ font-size: var(--step-small); color: var(--muted); line-height: 1.6; }}

.emo-propose {{ background: var(--surface-1); border: 1px solid var(--surface-2);
  border-radius: var(--radius-card); padding: 15px 18px 16px; margin: 2px 0 10px; max-width: 66ch; }}
/* Trace：一条记忆一行，分数是次要信息，所以压到 13px 的 faint */
.emo-traces {{ margin: 2px 0 10px; }}
.emo-trace {{ padding: 12px 0; border-top: 0; }}
.emo-trace:nth-child(odd) {{ background: var(--surface-1); padding-left: 14px; padding-right: 14px; }}
.emo-trace-head {{ display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  font-size: var(--step-small); color: var(--faint); }}
.emo-trace-score {{ margin-left: auto; color: var(--accent); font-variant-numeric: tabular-nums; }}
.emo-trace-text {{ margin: 7px 0 6px; font-size: 15px; line-height: 1.65; color: var(--ink); }}
.emo-trace-parts {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: var(--step-small);
  color: var(--faint); font-variant-numeric: tabular-nums; }}
.emo-trace-why {{ color: var(--muted); }}
/* 对话列收在一行 860px 内（和页头左对齐），不然 1600 宽的屏幕上两端气泡离得太远 */
.emo-col {{ width: 100%; max-width: 860px; }}
[data-testid="stChatInput"] {{ max-width: 860px; }}
/* 教练页也收在同一条 860px 线上（st.container(key="coach") 生成的类） */
.st-key-coach {{ max-width: 860px; }}
.emo-propose-label {{ font-size: var(--step-small); color: var(--muted); letter-spacing: 0.01em; }}
.emo-propose p {{ font-size: 17px; line-height: 1.7; color: var(--ink); margin: 7px 0 0; }}
.emo-propose-why {{ display: block; font-size: var(--step-small); color: var(--faint); margin-top: 8px; }}
.emo-foot {{ margin-top: 40px; font-size: var(--step-small); color: var(--faint); line-height: 1.9; }}
.emo-foot b {{ color: var(--muted); font-weight: 500; }}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
}}
</style>
"""


_CSS = ""


def mode() -> str:
    """当前外观（``浅色`` / ``深色``）。侧边栏的开关直接写这个 key。"""
    try:
        picked = st.session_state.get(MODE_KEY)
    except Exception:  # 不在 Streamlit 运行时（单测 / 脚本）时按默认走
        picked = None
    return picked if picked in MODES else DEFAULT_MODE


def palette(name: str | None = None) -> dict[str, str]:
    """取某套配色；名字不认识就回落默认那套。"""
    return DARK if (name or mode()) == "深色" else LIGHT


def use(name: str | None = None) -> str:
    """切到指定配色，并把模块级常量与 CSS 一起刷新；返回实际生效的名字。"""
    global _CSS
    global BG, SURFACE_1, SURFACE_2, SURFACE_3, SURFACE_4
    global INK, MUTED, FAINT, ACCENT, ACCENT_INK, ACCENT_SOFT, ACCENT_LINE
    global BUBBLE_LINE, BUBBLE_SHADOW
    chosen = name if name in MODES else mode()
    values = palette(chosen)
    BG = values["BG"]
    SURFACE_1 = values["SURFACE_1"]
    SURFACE_2 = values["SURFACE_2"]
    SURFACE_3 = values["SURFACE_3"]
    SURFACE_4 = values["SURFACE_4"]
    INK = values["INK"]
    MUTED = values["MUTED"]
    FAINT = values["FAINT"]
    ACCENT = values["ACCENT"]
    ACCENT_INK = values["ACCENT_INK"]
    ACCENT_SOFT = values["ACCENT_SOFT"]
    ACCENT_LINE = values["ACCENT_LINE"]
    BUBBLE_LINE = values["BUBBLE_LINE"]
    BUBBLE_SHADOW = values["BUBBLE_SHADOW"]
    _CSS = _build_css()
    return chosen


def css() -> str:
    """当前配色对应的完整 CSS 文本（chat.py 之类的组件拼样式时用）。"""
    return _CSS or use()


def inject(name: str | None = None) -> None:
    """切配色并注入 CSS；只在 app.py 里调一次，别的模块不要再散落 CSS。"""
    use(name)
    st.markdown(_CSS, unsafe_allow_html=True)


def brand(tagline: str) -> None:
    """侧边栏顶部：wordmark + 一句定位。"""
    st.markdown(
        f'<div class="emo-brand">Emo<span>Mem</span></div>'
        f'<div class="emo-tagline">{tagline}</div>',
        unsafe_allow_html=True,
    )


def page_head(*, name: str, who: str, meta: str, right: str) -> None:
    """页头：名字（30px）+ 一行状态，右侧一行五层计数。

    ``meta`` / ``right`` 允许极少量的 ``<b>``，调用方负责转义其余文本。
    """
    st.markdown(
        '<div class="emo-head">'
        '<div>'
        f'<div class="emo-head-name">{name}<span class="emo-who">{who}</span></div>'
        f'<div class="emo-head-meta">{meta}</div>'
        "</div>"
        f'<div class="emo-head-right">{right}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


# 导入时先按默认配色初始化一遍，保证 theme.XXX 永远不会是空串
use(DEFAULT_MODE)
