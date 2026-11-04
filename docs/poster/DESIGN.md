# EmoMem 答辩物料 · 设计说明（DESIGN.md）

本目录是本项目**答辩物料**的唯一源头：A2 海报（HTML → PDF/PNG）、一页口播稿（Markdown → DOCX）。
所有文案与数字都能追溯到 `docs/SPEC.md`、`core/config.py`、`docs/poster/metrics.json`——**海报上不出现任何手写数字**。

---

## 1. 设计目标

1. **三米外能读出主线**：A2 竖版只允许一条阅读动线——痛点 → 架构（记忆怎么被读出来/写回去）→ 六个机制 → 人设编译器 → 横向量化对比 → 指标与工程。任何一处让视线回跳都算失败。
2. **参数即证据**：评委最容易追问「这个数从哪来的」，所以每张机制卡片底部都带一行等宽字体的真实参数（`CONSOLIDATE_EVERY_TURNS = 4`、`W_SIM = 0.45`…），与 `core/config.py` 一字不差。
3. **深底 + 三色分层**：整张海报只有一种强调色（`--accent` 玫红），外加三层记忆各一个身份色（蓝/紫/黄）。除此之外不引入任何装饰色，禁止彩虹渐变、禁止 emoji 当图标。

---

## 2. 交付物

| 文件 | 尺寸 / 规格 | 体积 |
| --- | --- | --- |
| `docs/poster/poster.html` | 自包含单文件（内联 CSS + 内联 SVG），无外链资源 | 31.4 KB |
| `docs/poster/poster.pdf` | A2 竖版 420 × 594 mm，单页，MediaBox 1191.12 × 1684.08 pt | 509.8 KB |
| `docs/poster/poster.png` | 1588 × 2245 px（96 dpi 下的 A2），用于预览与投屏兜底 | 434.6 KB |
| `docs/答辩稿.md` | 3 分钟口播稿（含秒数与提词），正文 950 汉字 | — |
| `docs/答辩稿.docx` | A4 单页，四边 2 cm | 38.6 KB |
| `docs/poster/metrics.json` | 离线评测指标（由 `scripts/eval_memory.py` 生成） | — |

---

## 3. 配色变量表

海报的全部颜色都定义在 `poster.html` 的 `:root` 里（源码见 `build_poster.py` 的 `CSS` 常量）。改一处即全局生效。

| Token | 值 | 用途 |
| --- | --- | --- |
| `--ink` | `#0F172A` | 海报底色（深墨蓝） |
| `--panel` | `#16223B` | 卡片底色 |
| `--panel-2` | `#1B2A46` | 卡片强调态（检索打分节点） |
| `--line` | `#24334F` | 主分隔线、卡片描边 |
| `--line-soft` | `#1E2C47` | 表格行线、技术栈 chip 底 |
| `--text` | `#E8EDF7` | 主文字 |
| `--muted` | `#9AAAC6` | 正文说明文字 |
| `--dim` | `#6B7C99` | 弱化标注（参数行、页脚） |
| `--short` | `#38BDF8` | **短期 · 工作记忆**（工作记忆卡、关系弧左栏） |
| `--mid` | `#A78BFA` | **中期 · 情景记忆** |
| `--long` | `#F59E0B` | **长期 · 语义画像** |
| `--accent` | `#FB7185` | 唯一强调色：编号、箭头、指标数字、金句、小节 kicker |

约束：强调色只有 `--accent` 一个；三层记忆的色只用于「分层」语义（卡片顶边、小标题、bullet 点、左侧竖线），不用于装饰。表格里 EmoMem 一列用 `rgba(251,113,133,.07)` 淡底 + `#F5D6DC` 文字，不引入第二种强调色。

字体：`"Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif`；参数行与编号用 `"Consolas", "Cascadia Mono", monospace`（让 `superseded_by`、`W_SIM` 这类标识符有代码感）。

---

## 4. 栅格与字号层级

- **画布**：420 × 594 mm，`padding: 11mm 14mm 9mm 14mm`，内容区 **392 mm** 宽。
- **纵向分配**：`.poster` 是 `display:flex; flex-direction:column; justify-content:space-between`，**没有任何固定高度**——8 个区块按内容自然生长，剩余空间被 `space-between` 均分成 7 条缝，当前实测每缝 **7.6 mm**。这是本轮改造的关键：早期版本给每个区块写死 `height`，只要文案一变就溢出，现在版面自己找平衡。
- **分栏**：三栏 = `(392 − 2×26) / 3 = 113.3 mm`（`26 mm` 是卡缝，正好留给「固化 · 每 4 轮」「提炼 · 成 Fact」这类跨卡箭头）。六宫格 = 3 列 × 2 行，`gap 5mm`。指标条 = 6 格均分。
- **字号层级**（pt，A3 缩印后仍可读）：

| 层级 | 字号 | 说明 |
| --- | --- | --- |
| 主标题 `EmoMem` | 76 pt / 行高 .92 | 唯一超过 40pt 的元素 |
| 价值主张 `.value` | 25 pt | 强调色，132 mm 定宽 |
| 副标题 `.subtitle` | 20 pt | 「情感感知的三层记忆 + 人设编译器」 |
| 小节标题 `.sec-title` | 16.5 pt | 8 个板块各一个，配 8pt 大写 kicker + 细横线 |
| 卡片标题 / 图层名 | 13 / 12.5 pt | |
| 卡片编号 `.card-no` | 17 pt | 等宽，弱化到 90% 不透明度 |
| 正文 `.card-body` `.pain-text` | 9 / 9.5 pt | |
| 表体 `table.cmp` | 8.6 pt（维度列 9 pt） | 7 行 × 4 列 |
| 参数行 `.card-param` | 7.6 pt | 等宽，虚线上边 |
| 最小标注 | 7.2–8.6 pt | 只用于 kicker、页脚、指标说明以外的注解 |

- **架构图坐标**：`_arch_svg()` 的 `viewBox="0 0 392 112"`，**1 单位 = 1 mm**，与 CSS 的 `__TOKEN__mm` 一一对齐。所有栅格量集中在 `MM = {NODE_H 18, CANVAS_H 112, TIER_TOP 40, TIER_H 45, TIER_GAP 26, FADE_TOP 94, FADE_H 8, PARAMS_TOP 104}`，改高度**只改这里**，CSS 与 SVG 一起变。

---

## 5. 版式自检（改完必跑）

`python docs/poster/build_poster.py` 会依次做三件事并把结果打成一屏：

1. `[layout]` 用 `--dump-dom` 取回一份隐藏的 `#diag` JSON（由 `DIAG_JS` 在 `load` 后把每个区块的 `top/bot` 写进去），逐节打印 mm 级位置与缝宽；底边越过 594−9 mm、或相邻缝 < 1 mm 直接判失败。
2. **裁切检测**：只对 `white-space: nowrap/pre` 的横向溢出和 `overflow: hidden` 的纵向溢出报警（否则字形降部、`.step::after` 的绝对定位箭头会大量误报）。
3. `check_pdf()`：pypdf 读页数与 MediaBox，PyMuPDF 逐段比对文字块是否越出页面；`check_png()`：断言 1588 × 2245。

---

## 6. 重建命令

**推荐（一条命令做完 HTML → PDF → PNG → 自检）：**

```powershell
cd E:\Emotional-Agent-main
python docs\poster\build_poster.py            # 全量：--html + PDF + PNG + 版式/裁切校验
python docs\poster\build_poster.py --layout   # 只重出 HTML 并量版面，不出 PDF/PNG
python docs\poster\build_poster.py --html     # 只重出 poster.html
python docs\poster\build_poster.py --check    # 只校验已有产物
```

**底层两条 Chrome 命令**（脚本里封装的就是它们；本机无 LibreOffice，只能走 Chrome headless）：

```powershell
# 1) PDF（A2 单页，不带页眉页脚）
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --disable-gpu `
  --user-data-dir="$env:TEMP\emo-poster-profile" --no-pdf-header-footer `
  --print-to-pdf="E:\Emotional-Agent-main\docs\poster\poster.pdf" --virtual-time-budget=5000 `
  "file:///E:/Emotional-Agent-main/docs/poster/poster.html"

# 2) PNG 预览（96 dpi 下 A2 = 1588 × 2245 px）
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --disable-gpu `
  --user-data-dir="$env:TEMP\emo-poster-profile" --screenshot="E:\Emotional-Agent-main\docs\poster\poster.png" `
  --window-size=1588,2245 --force-device-scale-factor=1 --hide-scrollbars --virtual-time-budget=5000 `
  "file:///E:/Emotional-Agent-main/docs/poster/poster.html"
```

> Chrome 的 MediaBox 会取整到 **1191.12 × 1684.08 pt**（= 420.2 × 594.1 mm），不是教科书上的 1190.55 × 1683.78 pt；这是 Chrome 的 1/300 英寸取整，属正常。

**答辩稿（Markdown → 一页 DOCX）：**

```powershell
python docs\poster\make_docx.py           # 生成 docs\答辩稿.docx 并打印占版心百分比
python docs\poster\make_docx.py --check   # 只估算，不写文件
```

页数最终以 Word 为准（本机装了 Office，脚本估算只是守门员）：

```powershell
$docx=(Resolve-Path "docs\答辩稿.docx").Path
$w=New-Object -ComObject Word.Application; $w.Visible=$false
$d=$w.Documents.Open($docx,$false,$true); $d.ComputeStatistics(2); $d.Close(0); $w.Quit()
# 返回 1 = 单页
```

---

## 7. 指标数据从哪里来

`docs/poster/metrics.json` **由 `scripts/eval_memory.py` 离线跑出**（40 轮脚本化对话 + 5 段固化剧本，写入后 `build_poster.py` 自动读取渲染）。海报只消费、不生产数字；文件不存在时六格统一显示「待评测」。

| 键 | 海报上的位置 | 说明 |
| --- | --- | --- |
| `compress_ratio` | 指标 1（`65.4%`） | 记忆块字符 / 原始对话字符；说明行自动换算「上下文占用 · 省下 34.6%」 |
| `probe_hit` | 指标 2（`5/5`） | 5 条预埋探针的召回命中 |
| `conflict_resolved` | 指标 3（`1 条`） | 判为记忆修正、旧条目标 `superseded_by` 的条数 |
| `avg_strength_d0` | 指标 4（`0.998`） | 刚写入时的平均记忆强度 |
| `avg_strength_d30` | 指标 5（`0.295`） | 第 30 天的平均记忆强度（遗忘曲线落点） |
| `mood_gate_hits` | 指标 6（`5 次`） | 情绪门控跳过条数 |
| `turns` / `episodes` / `facts` | 指标区下方小字 | 「40 轮脚本化对话 / 5 段固化剧本」 |

**口径铁律**：`65.4%` 是**上下文占用**（记忆块 1251 字符 / 原始对话 1912 字符），等于省下 34.6%；**绝不能写成「压缩了 65%」**。说明行由 `_saved_pct()` 从 `compress_ratio` 反算，改数字时不用改文案。

---

## 8. 答辩稿的排版口径

- 结构固定 7 节：开场 15s → 三层记忆 45s → 三机制 60s → 人设编译器 30s → 数据与工程 30s → 收尾 15s，每节带「要点」提词行（不念）与一句加粗金句。
- A4、四边 2 cm、微软雅黑、正文 **10.5 pt**、小节标题 11.5 pt 加粗、标题 16 pt 加粗 + 玫红下框线。
- **行距用 1.0**（Word 的「多倍行距 1.0」= 微软雅黑单倍行高 1.714 em ≈ 18 pt，观感相当于普通字体的 1.7 倍行距）。原因：一页 A4 一页容纳 950 汉字 + 7 个标题行，用 1.25 会溢出 9 行；`estimate()` 里的 `line_factor = 1.714` 是从 Word 实测反推的（10.5 pt × 1.25 × 1.714 ≈ 22.5 pt 行距），所以估算值与 Word 实际分页一致。
- 段后间距设为 0，靠行距自带留白；`keep_with_next` 保证小节标题不和正文分页。
- 当前实测：Word 单页，正文最后一行距底部 30 pt（约 1.6 行余量）。

---

## 9. 后续修改指引

| 想改什么 | 改哪里 |
| --- | --- |
| 任何一句海报文案 | `build_poster.py` → `CONTENT` 字典（按区块分组，`arch_tiers` / `innov` / `compare` / `persona_*` / `stack` 等） |
| 指标数字 | **不要改海报**；改 `metrics.json` 或重跑 `scripts/eval_memory.py`，再 `python docs\poster\build_poster.py` |
| 指标格文案（标签、单位） | `build_poster.py` → `METRIC_SLOTS = [(key, unit, label), ...]` |
| 架构图高度 / 卡片位置 / 箭头坐标 | `build_poster.py` → `MM` 常量 + `_arch_svg()`；CSS 里的 `__TOKEN__mm` 会自动跟随 |
| 颜色 | `CSS` 常量里的 `:root` 变量（见 §3），别在别处写死颜色 |
| 字号层级 | `CSS` 常量里对应的选择器（见 §4 表），改完务必跑 `--layout` 看缝宽 |
| 口播稿正文 | `docs/答辩稿.md`（只改正文与要点行；`> 引用块` 与 `**要点**` 行不会进 docx） |
| 口播稿版式（字号/行距/边距） | `make_docx.py` 顶部常量：`BODY_PT` / `SECTION_PT` / `TITLE_PT` / `LINE_MULT` / `SPACE_AFTER_PT` / `MARGIN_CM` |

---

## 10. 踩过的坑（改之前先看这里）

1. **`__TOKEN__mm` 替换**：CSS 模板里自带 `mm` 单位，`CSS.replace(f"__{k}__", f"{v:g}")` 必须**只替换数字**。早期写成 `f"{v}mm"` 得到 `120mmmm`，属性失效，架构区塌成 10 mm。
2. **SVG 的 `font-size` 必须带单位**：`font-size="7.2"` 在 Chrome 里是无效的 presentation attribute，会回落到 16px（= 16 个 user unit = 16 mm），文字直接撑爆卡片。一律写 `font-size="7.2px"`。
3. **PWsh 里不要用 `python -c` 传中文**：GBK 解码会把中文和 `\n` 一起弄坏。带中文的改动一律写成临时 `.py` 文件再执行（现在已清理）。
4. **Chrome 输出必须按 UTF-8 解码**：`subprocess.run(..., encoding="utf-8", errors="replace")`，否则 GBK 解 `0xa2` 抛 `UnicodeDecodeError`；`main()` 里还要 `sys.stdout.reconfigure(encoding="utf-8")`。
5. **别给区块写固定 `height`**：文案一改就溢出压到下个标题上。用 `flex + space-between` 让版面自己分配。
6. **裁切检测要看 `white-space`**：不区分 `nowrap` / `overflow:hidden` 会把字形降部和绝对定位箭头当成溢出，全是噪音。
7. **docx 的「行距 1.25」不是 1.25 em**：Word 用字体自带行高（微软雅黑 1.714 em）做基数，`1.25` 实际是 22.5 pt 行距。估算页面占用时 `line_factor` 必须用 1.714。
8. **别信字符数估版面**：中英混排 + 标点挤压会让每行字数差 3–5 个，最终一定要用 `ComputeStatistics(2)` 让 Word 报页数。
