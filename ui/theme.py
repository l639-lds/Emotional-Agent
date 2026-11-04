"""界面主题：一套深色「夜里写信」的视觉系统，只在 app.py 里注入一次。

设计约束（来自对 taste-skill / impeccable 两个设计规则仓库的研究）：

* 一个 ground（#0F141B）、一个 ink（#E7ECF4）、**一个 accent（#E0A458）**；
  accent 只出现在主按钮、当前导航项和极少数状态点上（占屏 < 5%）。
* 层次只靠 tone step（surface 比 ground 高 3~6 个明度点）与间距，
  不用 1px 分隔线、不用静置阴影、不用渐变、不用玻璃拟态。
* 字号只有 5 档（13 / 16 / 20 / 30 / 40），正文 16px、行高 1.75，暗底再松一点。
* 界面里不出现 emoji 当图标（头像是「内容」，仍然保留）。
* 圆角只决定一次：全局 2px（偏「纸与墙」的直边），只有头像保留圆形。

Streamlit 的组件内部 DOM 会随版本变化，所以这里只覆盖稳定的
``data-testid``；改完必须真机截图复查。
"""

from __future__ import annotations

import streamlit as st

# 颜色 token：改这里就等于改全站
BG = "#0F141B"           # ground
SURFACE_1 = "#151C25"    # 卡片 / 侧边栏 / 助手气泡
SURFACE_2 = "#1B2430"    # 输入框 / 次级按钮 / 用户气泡
SURFACE_3 = "#222D3B"    # hover
INK = "#E7ECF4"
MUTED = "#93A0B4"        # 次要文字（对 BG 约 6.4:1）
FAINT = "#6B7789"        # 最弱一级（仅用于极小号辅助信息）
ACCENT = "#E0A458"       # 唯一强调色
ACCENT_INK = "#12100C"   # accent 上的文字

FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", '
    '"Noto Sans SC", sans-serif'
)

_CSS = f"""
<style>
:root {{
  --bg: {BG};
  --surface-1: {SURFACE_1};
  --surface-2: {SURFACE_2};
  --surface-3: {SURFACE_3};
  --ink: {INK};
  --muted: {MUTED};
  --faint: {FAINT};
  --accent: {ACCENT};
  --accent-ink: {ACCENT_INK};
  --radius: 2px;
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

/* 视图导航：把 radio 变成一列导航项（隐藏圆点） */
[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 2px; }}
[data-testid="stSidebar"] div[role="radiogroup"] > label {{
  padding: 9px 12px; border-radius: var(--radius); cursor: pointer;
  transition: background 150ms var(--ease), color 150ms var(--ease);
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{ background: var(--surface-2); }}
[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {{ display: none; }}
[data-testid="stSidebar"] div[role="radiogroup"] > label p {{
  font-size: var(--step-body); color: var(--muted); margin: 0;
}}
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{ background: var(--surface-2); }}
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--accent); font-weight: 600;
}}

/* ---------- 按钮：一个主按钮，其余是 tone step ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
  border: none; border-radius: var(--radius); font-family: {FONT_STACK};
  font-size: var(--step-body); font-weight: 500; padding: 9px 18px; min-height: 38px;
  background: var(--surface-2); color: var(--ink);
  transition: background 150ms var(--ease), color 150ms var(--ease), transform 120ms var(--ease);
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  background: var(--surface-3); color: var(--ink);
}}
.stButton > button:active {{ transform: translateY(1px); }}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] {{
  background: var(--accent); color: var(--accent-ink); font-weight: 600;
}}
.stButton > button[kind="primary"]:hover {{ background: var(--accent); color: var(--accent-ink); filter: brightness(1.06); }}
.stButton > button:disabled {{ background: var(--surface-1); color: var(--faint); }}

/* ---------- 输入类控件 ---------- */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {{
  background: var(--surface-1) !important; border: 1px solid var(--surface-2) !important;
  border-radius: var(--radius) !important; color: var(--ink) !important;
}}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea {{
  color: var(--ink) !important; font-size: var(--step-body); font-family: {FONT_STACK};
}}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {{
  border-color: var(--accent) !important;
}}
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

/* ---------- 对话输入框 ---------- */
[data-testid="stChatInput"] {{
  background: var(--surface-1); border: 1px solid var(--surface-2);
  border-radius: var(--radius); padding: 4px 6px;
}}
[data-testid="stChatInput"] textarea {{ font-size: var(--step-body); color: var(--ink); }}
[data-testid="stChatInput"]:focus-within {{ border-color: var(--surface-3); }}
/* 输入区自己的 focus 由容器边框提示，不要再套一层粗 outline */
[data-testid="stChatInput"] textarea:focus-visible,
[data-testid="stChatInput"] textarea:focus {{ outline: none !important; box-shadow: none !important; }}
[data-testid="stChatInput"] button {{ background: transparent !important; color: var(--muted) !important; }}
[data-testid="stChatInput"] button:hover {{ color: var(--accent) !important; }}

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
[data-testid="stExpander"] {{ background: var(--surface-1); border-radius: var(--radius); }}
[data-testid="stExpander"] details {{ border: none !important; background: transparent; }}
[data-testid="stExpander"] summary {{ font-size: var(--step-body); color: var(--ink); padding: 10px 14px; }}
[data-testid="stExpander"] summary:hover {{ background: var(--surface-2); }}
[data-testid="stExpander"] p {{ font-size: var(--step-small); color: var(--muted); }}
[data-testid="stAlert"] {{ border-radius: var(--radius); border: none; background: var(--surface-1); }}
[data-testid="stAlert"] p {{ font-size: var(--step-small); color: var(--ink); }}
[data-testid="stDataFrame"] {{ border-radius: var(--radius); }}
[data-testid="stFileUploaderDropzone"] {{
  background: var(--surface-1); border: 1px dashed var(--surface-3); border-radius: var(--radius);
}}
[data-testid="stFileUploaderDropzone"] p, [data-testid="stFileUploaderDropzone"] span {{
  font-size: var(--step-small) !important; color: var(--muted) !important;
}}
[data-testid="stPills"] button {{
  border-radius: var(--radius) !important; background: var(--surface-2) !important;
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

.emo-propose {{ background: var(--surface-1); padding: 15px 18px 16px; margin: 2px 0 10px; max-width: 66ch; }}
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


def inject() -> None:
    """把主题 CSS 注入页面（每次 rerun 都要注入，但只算一次字符串）。

    只在 app.py 里调一次；组件样式都在 ``_CSS`` 里，不要在别处再散落 CSS。
    """
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
