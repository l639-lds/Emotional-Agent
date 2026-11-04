# EmoMem 答辩物料 · 设计说明（DESIGN.md）

本目录是本项目**答辩物料**的唯一源头：A2 海报（HTML → PDF/PNG）、一页口播稿（Markdown → DOCX）。
所有文案与数字都能追溯到 `docs/SPEC.md`、`README.md`、`core/config.py`、`docs/poster/metrics.json`——**海报上不出现任何手写数字**。

当前版本对应**五层记忆（L0 原文库 / L1 会话层 / L2 长期事实 / L3 固化快照 / L4 自我认知）+ 记忆主权**这一版。

---

## 1. 设计目标

1. **三米外能读出主线**：A2 竖版只允许一条阅读动线——痛点 → 五层架构（记忆怎么被读出来 / 写回去）→ 八个创新点 → 记忆主权 → 人设编译器 → 横向量化对比 → 指标与工程。任何一处让视线回跳都算失败。
2. **参数即证据**：评委最容易追问「这个数从哪来的」，所以每张机制卡片底部都有一行等宽字体的真实参数（`CONSOLIDATE_EVERY_TURNS = 4`、`W_SIM = 0.45`、`ARCHIVE_TOP_K = 5`…），与 `core/config.py` 一字不差；指标区只读 `metrics.json`。
3. **深底 + 分层色**：整张海报只有一种强调色（`--accent` 玫红），五层记忆各一个身份色（冷灰 / 蓝 / 黄 / 紫 / 玫红）。除此之外不引入任何装饰色，禁止彩虹渐变、禁止 emoji 当图标。
4. **版面自己找平衡**：不给区块写固定 `height`，用 `flex + space-between` 把富余空间均分成 8 条缝（当前每缝 **7.0–7.1 mm**）。文案增删只影响缝宽，不会互相压。

---

## 2. 交付物

| 文件 | 尺寸 / 规格 | 体积 |
| --- | --- | --- |
| `docs/poster/poster.html` | 自包含单文件（内联 CSS + 内联 SVG），无外链资源 | 35.4 KB |
| `docs/poster/poster.pdf` | A2 竖版 420 × 594 mm，单页，MediaBox 1191.12 × 1684.08 pt | 537.2 KB |
| `docs/poster/poster.png` | 1588 × 2245 px（96 dpi 下的 A2），用于预览与投屏兜底 | 431.4 KB |
| `docs/答辩稿.md` | 3 分钟口播稿（7 段秒数 + 提词 + 金句），正文 983 汉字 | 4.8 KB |
| `docs/答辩稿.docx` | A4 单页（Word 实测 `PAGES=1`，37 行），四边 2 cm | 38.6 KB |
| `docs/poster/metrics.json` | 离线评测指标（由 `scripts/eval_memory.py` 生成，12 个键） | 284 B |
| `docs/poster/build_poster.py` | 海报生成器：HTML → PDF → PNG → 版式/裁切自检 | 58.7 KB |
| `docs/poster/make_docx.py` | 答辩稿生成器：Markdown → 一页 DOCX（含占比估算） | 8.4 KB |

---

## 3. 配色变量表

海报的全部颜色都定义在 `build_poster.py` 的 `CSS` 常量 `:root` 里，改一处即全局生效。

| Token | 值 | 用途 |
| --- | --- | --- |
| `--ink` | `#0F172A` | 海报底色（深墨蓝） |
| `--panel` | `#16223B` | 卡片底色 |
| `--panel-2` | `#1B2A46` | 卡片强调态（检索打分节点） |
| `--line` | `#24334F` | 主分隔线、卡片描边 |
| `--line-soft` | `#1E2C47` | 表格行线、技术栈 chip 底、遗忘区底 |
| `--text` | `#E8EDF7` | 主文字 |
| `--muted` | `#9AAAC6` | 正文说明文字 |
| `--dim` | `#6B7C99` | 弱化标注（参数行、页脚） |
| `--accent` | `#FB7185` | 唯一强调色：编号、箭头、指标数字、金句、小节 kicker、**L4 自我认知** |

**五层身份色**不进 `:root`，而是由 `arch_layers` 逐层内联注入 `style="--layer-color:#…"`（驱动卡片顶边、图层名、bullet 点）：

| 层 | 色 | 说明 |
| --- | --- | --- |
| L0 原文库 `ARCHIVE` | `#8FA3C4`（冷灰，虚线边框，底 `#141F36`） | 全量原文，永不删除、不注入 |
| L1 会话层 `SESSION` | `#38BDF8` | 最近 20 轮 + Episode |
| L2 长期事实 `FACT` | `#F59E0B` | 8 类事实，卡内含「冲突消解」chip |
| L3 固化快照 `SNAPSHOT` | `#A78BFA` | 每次固化落一份 JSON |
| L4 自我认知 `SELF NOTE` | `#FB7185`（= accent，底 `#1A2742`） | 它对自己的叙述，只在首轮注入 |

约束：强调色只有 `--accent` 一个；分层色只用于「分层」语义，不用于装饰。表格里 EmoMem 一列用 `rgba(251,113,133,.07)` 淡底 + `#F5D6DC` 文字。

字体：`"Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif`；参数行与编号用 `"Consolas", "Cascadia Mono", monospace`（让 `superseded_by`、`W_SIM` 这类标识符有代码感）。

---

## 4. 栅格与字号层级

- **画布**：420 × 594 mm，`padding: 11mm 14mm 9mm 14mm`，内容区 **392 mm** 宽。
- **纵向分配**：`.poster` 是 `display:flex; flex-direction:column; justify-content:space-between`，**没有任何固定高度**——9 个区块（head / pain / arch / innov / sov / persona / compare / eng / foot）按内容自然生长，富余空间被均分成 8 条缝。当前实测：head 60.8 / pain 40.4 / arch 131.7 / innov 80.6 / sov 30.2 / persona 48.2 / compare 62.1 / eng 56.9 / foot 6.6 mm，**每缝 7.0–7.1 mm**。
- **分栏**：五层卡 = `(392 − 4×5) / 5 = 74.4 mm`（`5 mm` 是卡缝，正好留给跨卡箭头的落点）；八卡 = 4 列 × 2 行，`gap 4.6mm`；主权三框 = 3 列；技术栈 6 chip = 6 列；指标 6 格均分。
- **字号层级**（pt）：

| 层级 | 字号 | 说明 |
| --- | --- | --- |
| 主标题 `EmoMem` | 68 pt / 行高 .90 | 唯一超过 40pt 的元素 |
| 价值主张 `.value` | 22 pt | 强调色，149 mm 定宽 |
| 副标题 `.subtitle` | 20 pt | 「情感感知的五层记忆 + 人设编译器」 |
| 小节标题 `.sec-title` | 16.5 pt + 8 pt kicker | 9 个板块各一个，配细横线与右侧 note |
| 指标数字 `.metric-v` | 17 pt 等宽 | 强调色；「待评测」时降到 12.5 pt |
| 卡片编号 `.card-no` | 14 pt 等宽 | 弱化到 90% 不透明度 |
| 痛点小标题 `.pain-title` | 13 pt | 三栏痛点各一个 |
| 图层名 `.layer-name` | 11.5 pt | 用该层的 `--layer-color` |
| 卡片标题 `.card-title` / 主权名 `.sov-name` | 11 pt | |
| 架构图说明 `.arch-caption` | 9 pt | 「前三层记的都是你，L4 记的是它」 |
| 痛点正文 `.pain-text` | 9.5 pt / 1.55 | |
| 指标说明 `.metric-l` | 9 pt（`nowrap`） | 每格一行，≤14 字 |
| 图层 bullet `.layer-item` | 8.2 pt / 1.5 | |
| 卡片正文 `.card-body` / 主权正文 `.sov-text` | 8 / 8.1 pt，行高 1.48 | |
| 表体 `table.cmp` | 8.3 pt（EmoMem 列 8.4 pt 加粗） | 7 行（表头 + 6 维度）× 5 列 |
| 底部两行说明 `.eng-note` | 8.6 pt（`nowrap`） | 评测说明 + 五层规模 |
| 路线图例 `.fade-strip` | 7.6 pt 两行 | 5 条路线的完整解释 |
| 参数行 `.card-param` | 7 pt 等宽 | 虚线上边，贴卡片底 |
| 最小标注 | 6.4–8.5 pt | layer-en、kicker、页脚、表格 note |

- **架构图坐标**：`_arch_svg()` 的 `viewBox="0 0 392 113"`，**1 单位 = 1 mm**，与 CSS 的 `__TOKEN__mm` 一一对齐。栅格量集中在 `MM = {NODE_H 18, CANVAS_H 113, LAYER_TOP 44, LAYER_H 39, LAYER_GAP 5, FADE_TOP 89.6, FADE_H 10.4, PARAMS_TOP 101.5}`；派生 `LAYER_W=(392-4*5)/5=74.4`、`LAYER_C=[37.2,116.6,196,275.4,354.8]`、`NODE_C=[30,96.4,162.8,229.2,295.6,362]`。改高度**只改 `MM`**，CSS 与 SVG 一起变。
- **走廊（18.4–43.4 mm）三层走线**：`y_taps=25.5`（L4 首轮注入 + L0 回溯共用一层，两段的 x 区间不重叠）、`y_back=32.0`（写回虚线回路，从最后一列卡片右侧 x=362 绕行）、`y_consolid=38.5`（固化实线，在 x=275.4 / x=354.8 处下箭头进 L3 / L4）。标签一律**水平、2~4 字短名**（召回 / 写回 / 固化 / 回溯 / 首轮注入），每条再画一块 `fill="#0F172A"` 遮罩矩形压线上（走廊底色就是 `rgb(15,23,42)`，遮罩不可见但保证不压线），完整解释放在图下 `.fade-strip` 两行图例里。
- **遗忘区**：`fade-strip` 起于 `FADE_TOP=89.6`，只画 L1 / L2 两支向下箭头（**L0 永不删除，所以不画**），标签在箭头右侧 3 mm、基线在 `FADE_TOP-2`。

---

## 5. 版式自检（改完必跑）

`python docs/poster/build_poster.py` 会依次做三件事并把结果打成一屏：

1. `[layout]` 用 `--dump-dom` 取回一份隐藏的 `#diag` JSON（由 `DIAG_JS` 在 `load` 后把每个区块的 `top/bot` 写进去），逐节打印 mm 级位置与缝宽；底边越过 594−9 mm、或相邻缝 < 1 mm 直接判失败。
2. **裁切检测**：只对 `white-space: nowrap/pre` 的横向溢出和 `overflow: hidden` 的纵向溢出报警（否则字形降部、`.step::after` 的绝对定位箭头会大量误报）。
3. `check_pdf()`：pypdf 读页数与 MediaBox，PyMuPDF 逐段比对文字块是否越出页面；`check_png()`：断言 1588 × 2245。

机器自检只保证「不越界、不裁切」，**压字、层级、配色还得人眼看**：每次改完用 `read_image docs/poster/poster.png` 复核一遍，重点看架构图走廊与对比表。

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

`docs/poster/metrics.json` **由 `scripts/eval_memory.py` 离线跑出**（40 轮脚本化对话 + 5 段固化剧本），`build_poster.py` 的 `load_metrics()` 兼容三种壳（`{key:value}` / `{"metrics":{…}}` / `{"metrics":[{key,value}]}`）并自动渲染。海报只消费、不生产数字；文件缺失时六格统一显示「待评测」。

| 键 | 海报上的位置 | 说明 |
| --- | --- | --- |
| `compress_ratio` | 指标 1（`66.7%`） | 记忆块字符 / 原始对话字符；说明行由 `_saved_pct()` 自动换算「上下文占用 · 省下 33.3%」 |
| `probe_hit` | 指标 2（`5/5`） | 5 条预埋探针的召回命中 |
| `conflict_resolved` | 指标 3（`1 条`） | 判为记忆修正、旧条目标 `superseded_by` 的条数 |
| `avg_strength_d0` | 指标 4（`0.998`） | 刚写入时的平均记忆强度 |
| `avg_strength_d30` | 指标 5（`0.295`） | 第 30 天的平均记忆强度（遗忘曲线落点） |
| `mood_gate_hits` | 指标 6（`5 次`） | 情绪门控跳过条数 |
| `turns` / `episodes` | 指标区第一行小字 | 「40 轮脚本化对话 / 5 段固化剧本」 |
| `archive_messages` / `snapshots` / `self_notes` | 指标区第二行小字（`scale_note`） | 五层规模：L0 原文 40 条 · L3 快照 5 份 · L4 自述 5 条 |
| `facts` | 后备 | 当前未上版，`_format_metric` 已支持 |

**口径铁律**：`66.7%` 是**上下文占用**（记忆块 1275 字符 / 原始对话 1912 字符），等于省下 33.3%；**绝不能写成「压缩了 66%」**。说明行由 `_saved_pct()` 从 `compress_ratio` 反算，改数字时不用改文案。

---

## 8. 答辩稿的排版口径

- 结构固定 7 节、总时长 3:00，每节带「要点」提词行（不念）与一句加粗金句：
  `0:00–0:15 开场` → `0:15–0:55 从三层到五层` → `0:55–1:15 固化一次调用产出六样` → `1:15–2:00 三机制（情绪门控 / 冲突消解 / 可解释 Trace）` → `2:00–2:20 记忆主权（可查 · 可删 · 可改）` → `2:20–2:45 人设编译器` → `2:45–3:00 数字与收尾`。
- 正文 **983 汉字**（要求 900–1100），数字全部来自 `metrics.json`：66.7% / 省下 33.3% / 探针 5 条全中 / 冲突消解 1 条 / 30 天后 0.295。
- A4、四边 2 cm、微软雅黑；标题 **15.5 pt** 加粗 + 玫红下框线、小节标题 **11 pt** 加粗（`#A8324A`）、正文 **10 pt**、段后 0、小节前 1.5 pt。
- **行距用 1.0**（Word 的「多倍行距 1.0」= 微软雅黑单倍行高 1.714 em，观感相当于普通字体的 1.7 倍）。原因：一页 A4 要装 980 汉字 + 7 个标题行，1.25 会溢出 9 行；`estimate()` 里的 `line_factor = 1.714` 是从 Word 实测反推的，估算值与 Word 分页一致（当前估算 99%，Word 实测单页 37 行）。
- `keep_with_next` 保证小节标题不和正文分页。

---

## 9. 后续修改指引

| 想改什么 | 改哪里 |
| --- | --- |
| 任何一句海报文案 | `build_poster.py` → `CONTENT` 字典（`arch_chain` / `arch_layers` / `arch_routes` / `innovations` / `sov_items` / `persona_*` / `compare_rows` / `stack` / `footer`） |
| 五层卡片的名字、颜色、bullet | `CONTENT["arch_layers"]`：`(名称, 英文, 色值, bullet 列表, 样式类)`，第 3 项直接变成 `--layer-color` |
| 架构图上的路线说明 | `CONTENT["arch_routes"]`（前 4 条进图例第一行，第 5 条「遗忘」与 `fade_note` 进第二行）；箭头坐标在 `_arch_svg()` |
| 指标数字 | **不要改海报**；改 `metrics.json` 或重跑 `scripts/eval_memory.py`，再 `python docs\poster\build_poster.py` |
| 指标格文案（标签、单位） | `build_poster.py` → `METRIC_SLOTS = [(key, unit, label), ...]`；小字用的键在 `METRIC_EXTRAS` |
| 架构图高度 / 卡片位置 / 箭头坐标 | `build_poster.py` → `MM` 常量 + `_arch_svg()`；CSS 里的 `__TOKEN__mm` 会自动跟随 |
| 颜色 | `CSS` 常量里的 `:root`（见 §3）与 `arch_layers` 的色值，别在别处写死颜色 |
| 字号层级 | `CSS` 常量里对应的选择器（见 §4 表），改完务必跑 `--layout` 看缝宽 |
| 口播稿正文 | `docs/答辩稿.md`（只改正文与要点行；`> 引用块` 与 `**要点**` 行不会进 docx） |
| 口播稿版式（字号/行距/边距） | `make_docx.py` 顶部常量：`BODY_PT` / `SECTION_PT` / `TITLE_PT` / `LINE_MULT` / `SPACE_AFTER_PT` / `MARGIN_CM` |

---

## 10. 踩过的坑（改之前先看这里）

1. **`__TOKEN__mm` 替换**：CSS 模板里自带 `mm` 单位，`CSS.replace(f"__{k}__", f"{v:g}")` 必须**只替换数字**。早期写成 `f"{v}mm"` 得到 `120mmmm`，属性失效，架构区塌成 10 mm。
2. **SVG 的 `font-size` 必须带单位**：`font-size="7.2"` 在 Chrome 里是无效的 presentation attribute，会回落到 16px（= 16 个 user unit = 16 mm），文字直接撑爆卡片。一律写 `font-size="7.2px"`。
3. **走廊标签只能放短名**：五层化后走廊只有 25 mm 高、4 条横线，整句标签（80–90 mm 宽）必然互相重叠、还会被别的线横穿。做法 = 2~4 字短名 + `#0F172A` 遮罩矩形 + 图下两行完整图例。遮罩色必须等于实测底色 `rgb(15,23,42)`，不要用 `--panel`。
4. **忘掉固定 `height`**：文案一改就溢出压到下个标题上。用 `flex + space-between` 让版面自己分配；区块多了就整体微缩字号/内边距（本轮缩了 17 处）。
5. **PWsh 里不要用 `python -c` 传中文**：GBK 解码会把中文和 `\n` 一起弄坏，也**不支持 `python - <<'PY'` heredoc**。带中文的改动一律写成临时 `.py` 文件再执行。
6. **Chrome 输出必须按 UTF-8 解码**：`subprocess.run(..., encoding="utf-8", errors="replace")`，否则 GBK 解 `0xa2` 抛 `UnicodeDecodeError`；`main()` 里还要 `sys.stdout.reconfigure(encoding="utf-8")`。
7. **裁切检测要看 `white-space`**：不区分 `nowrap` / `overflow:hidden` 会把字形降部和绝对定位箭头当成溢出，全是噪音。
8. **docx 的「行距 1.25」不是 1.25 em**：Word 用字体自带行高（微软雅黑 1.714 em）做基数，`1.25` 实际是 22.5 pt 行距。估算页面占用时 `line_factor` 必须用 1.714。
9. **别信字符数估版面**：中英混排 + 标点挤压会让每行字数差 3–5 个，最终一定要用 `ComputeStatistics(2)` 让 Word 报页数。
10. **文本替换脚本别硬编码引号**：md 里可能是 ASCII `"` 或中文 `“”`，`assert s.count(old)==1` 会因为字形不同报「命中 0 次」。匹配串避开引号，或用 `re.sub` 统一规范化。
