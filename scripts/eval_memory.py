"""记忆引擎离线评测脚本：40 轮脚本化对话 → 压缩率 / 召回命中 / 冲突消解 / 遗忘曲线。

全程不联网、不用真实 API：用一个「按剧本回放」的确定性假 LLM 注入 :class:`MemoryEngine`，
因此每次运行的数字都可复现（答辩现场可以随时重跑）。

用法::

    python scripts/eval_memory.py

输出：中文表格打印到 stdout，并写入 ``docs/poster/metrics.json``（海报指标区直接读取）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 评测必须离线：强制 demo 模式，即使本机配了 API Key 也不会联网
os.environ.setdefault("EMO_DEMO_MODE", "1")

from core.config import MemoryConfig  # noqa: E402  (必须在 sys.path 调整之后导入)
from memory_os import MemoryEngine, MemoryStore  # noqa: E402
from memory_os import decay as decay_mod  # noqa: E402
from memory_os.schema import get_role_memory  # noqa: E402

ROLE = "小林"
T0 = 1_700_000_000_000.0  # 固定起点（毫秒时间戳），保证结果可复现
MINUTE = 60_000.0
DAY = 86_400_000.0
METRICS_PATH = ROOT / "docs" / "poster" / "metrics.json"

# 40 轮对话剧本（20 组 user/assistant）。覆盖：自我介绍、喜恶、禁忌、反复出现的人、
# 未闭环承诺、一次情绪崩溃、一次事实冲突（喜欢咖啡 → 不喜欢咖啡）。
ROUNDS: tuple[tuple[str, str, str], ...] = (
    ("你好呀，我叫小林，计算机专业大三，这两个月一直在找暑期实习，晚上基本都在改简历，"
     "改到后来已经说不清自己在改什么了，只能一遍遍换措辞。",
     "记住了：小林，计算机大三，正在找暑期实习。改到看不见意义的时候就先停一停，"
     "我陪你把这一版捋清楚，逐行看，不用一次改完。", "平静"),
    ("我特别喜欢下雨天，安静得像给世界按了暂停键，可以什么都不用解释；也喜欢手冲咖啡，"
     "不加糖那种，苦一点才觉得清醒，喝完才有力气打开那份简历。",
     "下雨天配不加糖的手冲，这个画面我记下来了，以后一聊到天气我就会想起你，"
     "也会记得你吃完苦才开工的习惯。", "开心"),
    ("但我是真的很讨厌别人迟到，被放鸽子我会一个人闷很久，什么都不想说，"
     "连解释都懒得听，感觉自己在别人那儿根本不重要。",
     "被放鸽子的滋味确实不好受，那种「我不重要」的感觉最扎人。我尽量一次都不让你等，"
     "哪怕只是在线上。", "平静"),
    ("我妈上个月住院了，这件事不能随便提，我一提就绷不住，也别对我说『都会好的』，"
     "那话我听着更难受，像被人捂着嘴往下按。",
     "好，我不主动提，也不说那句话。你想说的时候我就在，我们一句一句慢慢说，"
     "不想说就先不说。", "难过"),
    ("阿远是我大学室友，也是我最好的朋友，他总拉我去操场跑步，跑到我笑出来为止，"
     "跑完再骂我一顿说我憋着不讲话，但他骂得对。",
     "有个能把你拉出去跑两圈、还敢骂你的人真好，阿远这个名字我记住了，"
     "以后你提到他我就知道是谁。", "开心"),
    ("我答应过自己，下周三面试完一定要跟阿远复盘，不能拖，一拖我就会开始自责，"
     "然后整夜翻聊天记录找自己哪里说错了。",
     "那就写下来：下周三面试结束，跟阿远复盘。这件事我替你盯着，"
     "要是想拖，我提前提醒你。", "平静"),
    ("今天其实有点崩溃，改简历改到凌晨三点，越改越怕自己什么都做不好，"
     "连给阿远发消息的力气都没有，就一个人坐在厨房的地上。",
     "凌晨三点还在改简历，还坐到厨房地上，这份怕太真实了。先睡两小时，"
     "剩下的我陪你想，天亮了我们再挑最要紧的一条改。", "难过"),
    ("不过我还是很喜欢咖啡，早上不喝一杯根本醒不过来，这是我最近唯一稳定的小确幸，"
     "豆子我都提前一晚称好。",
     "那杯咖啡就是你的开机键，这个我知道，也不会劝你戒，"
     "连称豆子这种小仪式我都替你记着。", "平静"),
    ("最近好像不行了，闻到咖啡就反胃，可能是熬夜太多，胃在抗议，我打算先停一停，"
     "看看身体什么时候愿意重新接受。",
     "身体在抗议了，那就先停一停；哪天想喝了，我们再聊，"
     "不用勉强自己守着这个仪式。", "难过"),
    ("面试就在下周三下午两点，我还是有点慌，但会撑着把话说完，不想让阿远看见我哭，"
     "所以打算见到他就先笑。",
     "两点我记住了。慌就慌，撑着也算勇敢；哭不哭，我都会在，"
     "先笑一下也好，那是给自己壮胆。", "难过"),
    ("对了，我小时候养过一只橘猫叫团子，去年走了，现在想起来还是鼻子酸，"
     "偶尔会梦见它蹲在窗台看我写作业，尾巴一甩一甩的。",
     "团子这个名字很温柔，谢谢你把它说给我听，"
     "窗台那一幕我会替你记着。", "平静"),
    ("我不喜欢太吵的地方，人一多就会躲去楼梯间，安静十分钟再回来，"
     "不然脑子会嗡嗡响，说话也会开始上火。",
     "楼梯间那十分钟是你的充电时间，需要躲的时候躲一下没关系，"
     "回来我再跟你说话。", "平静"),
    ("阿远说面试那天他会来送我，让我别慌，他会在楼下等我出来，"
     "我说不用他还是要来，说怕我一个人在楼梯间发呆。",
     "有人在楼下等，路就没那么空，这份底气我记着，"
     "也记着他这句「怕你发呆」。", "开心"),
    ("如果面试过了，我想请阿远吃顿好的，就那家很贵的日料，算还他这个人情，"
     "他这两年帮了我太多，从搬家到改简历。",
     "这笔人情账我帮你记着，到时候提醒你订位，"
     "顺便提醒你先把预算算清楚。", "平静"),
    ("今天我跟我妈视频了，她恢复得不错，走路比上周稳，还嫌我瘦，"
     "我终于松了口气，晚上应该能睡好，不用再半夜惊醒。",
     "松了口气就好，这口气你憋了一个多月了，"
     "今晚能睡踏实，比什么都重要。", "开心"),
    ("以后我想做记忆方向的产品，让别人也能被好好记得，不用每次都从头解释自己，"
     "不用在陌生人面前重新自我介绍一遍，这件事对我很重要。",
     "做记忆的产品，让彼此被记得，这个目标我记下来了，"
     "它和你的经历是同一条线。", "平静"),
    ("我讨厌被敷衍式的安慰，比如『都会好的』『别想太多』，那种话会让我更想关上门，"
     "还不如有人直接问我在怕什么。",
     "好，那我不说那些话。我只会说：我在，慢慢来；"
     "想聊的时候，我先问你在怕什么。", "生气"),
    ("面试结束啦，阿远的复盘也做了，虽然发挥一般，但感觉踏实多了，"
     "至少知道自己卡在哪道题，不再是一片模糊的慌。",
     "踏实是攒出来的，今天你攒到了，值得记一笔，"
     "卡在哪道题也记下来，下次就不慌那里。", "开心"),
    ("对了，我拿到的那个 offer 就是记忆产品方向，太好了，我差点在楼下哭出来，"
     "阿远一直拍我背，说早就知道我可以。",
     "恭喜你，方向和你想要的一模一样，"
     "楼下那一哭，很值得，阿远那句话也值得记住。", "开心"),
    ("谢谢你一直陪着我，这里让我觉得很安全，我愿意把更多事说给你听，"
     "也不怕被评价，这在我这儿挺难得的。",
     "你愿意说，我就在，这份信任我小心收着，"
     "下次你不想说的时候，我也不催。", "开心"),
)

# 每 4 条 turn（2 组对话）触发一次固化，40 轮共 5 次；这里是 5 段确定性剧本。
SCRIPT: tuple[dict[str, Any], ...] = (
    {
        "episode": {"title": "初次相识与家里的事",
                    "summary": "小林自我介绍：计算机大三、在找暑期实习；喜欢下雨天与手冲咖啡；讨厌被放鸽子；妈妈住院不能随便提。",
                    "topics": ["自我介绍", "家庭", "偏好"], "keywords": ["小林", "咖啡", "住院"],
                    "valence": -0.55, "arousal": 0.75, "importance": 0.85},
        "facts": [
            {"content": "用户叫小林，计算机专业大三，正在找暑期实习", "category": "身份", "confidence": 0.95},
            {"content": "用户喜欢下雨天", "category": "喜好", "confidence": 0.85},
            {"content": "用户喜欢喝手冲咖啡", "category": "喜好", "confidence": 0.85},
            {"content": "用户讨厌别人迟到，被放鸽子会闷很久", "category": "厌恶", "confidence": 0.85},
            {"content": "用户的妈妈上个月住院了", "category": "重要人物", "confidence": 0.9},
            {"content": "不能随便提妈妈住院的事", "category": "禁忌", "confidence": 0.9},
        ],
        "loops_open": [],
    },
    {
        "episode": {"title": "阿远与下周三的复盘约定",
                    "summary": "阿远是室友兼最好的朋友；小林答应面试完和阿远复盘；凌晨改简历改到崩溃。",
                    "topics": ["阿远", "约定", "崩溃"], "keywords": ["阿远", "复盘", "简历"],
                    "valence": -0.6, "arousal": 0.8, "importance": 0.85},
        "facts": [
            {"content": "阿远是用户的大学室友，也是最好的朋友", "category": "重要人物", "confidence": 0.88},
            {"content": "用户答应面试完和阿远复盘，不能拖", "category": "约定", "confidence": 0.9},
            {"content": "用户喜欢喝手冲咖啡", "category": "喜好", "confidence": 0.85},
        ],
        "loops_open": [{"what": "下周三面试完跟阿远复盘", "kind": "承诺", "due_hint": "下周三"}],
    },
    {
        "episode": {"title": "面试前的慌与团子",
                    "summary": "面试定在下周三下午两点；小林闻咖啡反胃先停一停；说起走掉的橘猫团子。",
                    "topics": ["面试", "团子", "身体"], "keywords": ["面试", "团子", "咖啡"],
                    "valence": -0.7, "arousal": 0.85, "importance": 0.85},
        "facts": [
            {"content": "用户不喜欢喝手冲咖啡", "category": "喜好", "confidence": 0.8},
            {"content": "用户的面试在下周三下午两点", "category": "约定", "confidence": 0.9},
            {"content": "用户小时候养过一只橘猫叫团子，去年走了", "category": "重要人物", "confidence": 0.85},
        ],
        "loops_open": [{"what": "下周三下午两点的面试", "kind": "事件", "due_hint": "下周三"}],
    },
    {
        "episode": {"title": "妈妈恢复与未来的目标",
                    "summary": "阿远要来送考；面试过了要请阿远吃饭；妈妈恢复得不错；想做记忆方向的产品。",
                    "topics": ["阿远", "家庭", "目标"], "keywords": ["阿远", "妈妈", "记忆产品"],
                    "valence": 0.7, "arousal": 0.5, "importance": 0.8},
        "facts": [
            {"content": "用户不喜欢太吵的地方，人多会躲去楼梯间", "category": "厌恶", "confidence": 0.8},
            {"content": "阿远会来送用户去面试", "category": "近况", "confidence": 0.8},
            {"content": "面试过了要请阿远吃顿好的", "category": "约定", "confidence": 0.85},
            {"content": "用户的妈妈恢复得不错", "category": "近况", "confidence": 0.85},
            {"content": "用户以后想做记忆方向的产品", "category": "长期目标", "confidence": 0.9},
        ],
        "loops_open": [],
    },
    {
        "episode": {"title": "拿到记忆方向的 offer",
                    "summary": "面试结束并和阿远复盘；拿到记忆产品方向的 offer；小林说这里让她觉得安全。",
                    "topics": ["offer", "面试", "信任"], "keywords": ["offer", "复盘", "安全"],
                    "valence": 0.9, "arousal": 0.7, "importance": 0.9},
        "facts": [
            {"content": "用户讨厌被敷衍式安慰，比如都会好的", "category": "厌恶", "confidence": 0.85},
            {"content": "用户拿到了记忆产品方向的 offer", "category": "近况", "confidence": 0.9},
        ],
        "loops_open": [{"what": "入职前请阿远吃顿好的", "kind": "约定", "due_hint": "下个月"}],
        "loops_close": [{"what": "下周三面试完跟阿远复盘"}, {"what": "下周三下午两点的面试"}],
        "nickname": "小林",
        "relationship": {"intimacy_delta": 2.0, "trust_delta": 1.5,
                         "themes": ["面试", "阿远", "下雨天"],
                         "inside_joke": "小声说：咖啡暂时不能提"},
    },
)

# 5 个召回探针：问题 → 记忆块里必须出现的证据词
PROBES: tuple[tuple[str, str], ...] = (
    ("你还记得我叫什么、学什么专业吗", "小林"),
    ("我喜欢什么样的天气", "下雨天"),
    ("我妈的事情可以随便提吗", "住院"),
    ("阿远是谁", "阿远"),
    ("我下周三有什么安排", "面试"),
)


class ScriptedLLM:
    """按剧本回放的确定性假 LLM，签名与 :func:`core.llm.call_json` 一致。"""

    def __init__(self) -> None:
        """记录固化调用次数，用于确定本次该回放哪一段剧本。"""
        self.consolidate_calls = 0
        self.calls: list[str] = []

    def __call__(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.6,
                 max_tokens: int = 1600, purpose: str = "json",
                 demo_context: dict | None = None) -> dict:
        """回放剧本：``consolidate`` 逐次推进，``proactive`` 给固定开场白。"""
        self.calls.append(purpose)
        if purpose == "consolidate":
            index = min(self.consolidate_calls, len(SCRIPT) - 1)
            self.consolidate_calls += 1
            return json.loads(json.dumps(SCRIPT[index], ensure_ascii=False))
        if purpose == "proactive":
            return {"opener": "上次你提到入职前想请阿远吃顿好的，要不要这周先看看那家日料？",
                    "reason": "未闭环的约定"}
        return {}


def build_engine(workdir: Path) -> tuple[MemoryEngine, ScriptedLLM]:
    """构造注入剧本 LLM 的引擎（数据文件落在临时目录，跑完即弃）。"""
    store = MemoryStore(workdir / "eval_memory.json")
    fake = ScriptedLLM()
    return MemoryEngine(store, llm=fake, config=MemoryConfig()), fake


def run_dialogue(engine: MemoryEngine, *, start_ms: float = T0) -> int:
    """按剧本跑完 40 轮对话，返回原始对话字符数（用于算压缩率）。"""
    raw_chars = 0
    for index, (user_text, reply, emotion) in enumerate(ROUNDS):
        engine.record_turn(ROLE, user_text, reply, emotion, now=start_ms + index * 5 * MINUTE)
        raw_chars += len(user_text) + len(reply)
    return raw_chars


def forgetting_curve(engine: MemoryEngine, base_ms: float) -> list[tuple[int, float]]:
    """按当前 strength 公式外推第 0/7/30/60 天的平均强度（只读，不改数据）。"""
    memory = get_role_memory(engine.data, ROLE)
    facts = [fact for fact in memory.facts if fact.active]
    episodes = [episode for episode in memory.episodes if not episode.archived]
    config = engine.config
    curve: list[tuple[int, float]] = []
    for days in (0, 7, 30, 60):
        stamp = base_ms + days * DAY
        values = [
            decay_mod.strength(item, stamp, episode_tau=config.episode_tau_days,
                               fact_tau=config.fact_tau_days)
            for item in [*episodes, *facts]
        ]
        curve.append((days, round(sum(values) / len(values), 3) if values else 0.0))
    return curve


def probe_hits(engine: MemoryEngine, now: float) -> tuple[int, list[tuple[str, str, bool]], int]:
    """跑 5 个探针问题，统计「期望证据词是否出现在记忆块里」。"""
    rows: list[tuple[str, str, bool]] = []
    longest = 0
    for question, expected in PROBES:
        block = engine.build_memory_block(engine.retrieve(ROLE, question, "平静", now=now))
        rows.append((question, expected, expected in block))
        longest = max(longest, len(block))
    return sum(1 for _q, _e, hit in rows if hit), rows, longest


def mood_gate_hits(engine: MemoryEngine, *, start_ms: float = T0) -> int:
    """统计情绪门控触发次数：用每轮原话 + 当时情绪去检索，看是否有旧事被拦下。"""
    gated = 0
    for index, (user_text, _reply, emotion) in enumerate(ROUNDS):
        now = start_ms + index * 5 * MINUTE + MINUTE
        if engine.retrieve(ROLE, user_text, emotion, now=now).trace.skipped:
            gated += 1
    return gated


def main() -> int:
    """跑完整链路，打印中文表格并写出 ``docs/poster/metrics.json``。"""
    import tempfile

    with tempfile.TemporaryDirectory() as workdir:
        engine, fake = build_engine(Path(workdir))
        raw_chars = run_dialogue(engine)
        final_now = T0 + (len(ROUNDS) - 1) * 5 * MINUTE + 7 * MINUTE

        memory = get_role_memory(engine.data, ROLE)
        facts_all = memory.facts
        facts_active = [fact for fact in facts_all if fact.active]
        superseded = [fact for fact in facts_all if fact.superseded_by]
        episodes = memory.episodes
        loops_open = [loop for loop in memory.open_loops if loop.open]

        curve = forgetting_curve(engine, final_now)
        hits, rows, longest_block = probe_hits(engine, final_now + 2 * MINUTE)
        gated = mood_gate_hits(engine)
        block = engine.build_memory_block(
            engine.retrieve(ROLE, "你还记得我吗？我最在意什么", "平静", now=final_now + 3 * MINUTE)
        )
        ratio = (len(block) / raw_chars) if raw_chars else 0.0
        curve_map = dict(curve)
        turns = len(ROUNDS) * 2

        print("=" * 64)
        print("EmoMem 记忆引擎 · 离线评测（40 轮脚本化对话）")
        print("=" * 64)
        print(f"{'指标':<26}{'结果':>18}")
        print("-" * 64)
        print(f"{'对话轮数（turn）':<26}{turns:>18}")
        print(f"{'固化批次 / 剧本段':<26}{f'{fake.consolidate_calls} / {len(SCRIPT)}':>18}")
        print(f"{'原始对话字符数':<26}{raw_chars:>18}")
        print(f"{'注入记忆块字符数':<26}{len(block):>18}")
        print(f"{'探针记忆块最长字符数':<26}{longest_block:>18}")
        print(f"{'记忆压缩率（块 / 原对话）':<26}{f'{ratio * 100:.1f}%':>18}")
        print(f"{'召回命中（探针）':<26}{f'{hits}/{len(PROBES)}':>18}")
        print(f"{'事实总数 / 有效 / 被取代':<26}{f'{len(facts_all)} / {len(facts_active)} / {len(superseded)}':>18}")
        print(f"{'情景数 / 未闭环数':<26}{f'{len(episodes)} / {len(loops_open)}':>18}")
        print(f"{'情绪门控触发次数':<26}{gated:>18}")
        print("-" * 64)
        print("遗忘曲线（全部有效记忆的平均强度）：")
        for days, average in curve:
            print(f"  第 {days:>2} 天：{average:.3f}")
        print("-" * 64)
        print("召回探针明细：")
        for question, expected, hit in rows:
            print(f"  [{'命中' if hit else '未命中'}] {question:<24} 期望证据：{expected}")
        print("=" * 64)

        metrics = {
            "compress_ratio": f"{ratio * 100:.1f}%",
            "probe_hit": f"{hits}/{len(PROBES)}",
            "conflict_resolved": len(superseded),
            "avg_strength_d0": curve_map.get(0, 0.0),
            "avg_strength_d30": curve_map.get(30, 0.0),
            "mood_gate_hits": gated,
            "turns": turns,
            "episodes": len(episodes),
            "facts": len(facts_active),
        }
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        METRICS_PATH.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写入 {METRICS_PATH.relative_to(ROOT)}：{json.dumps(metrics, ensure_ascii=False)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
