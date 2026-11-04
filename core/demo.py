"""离线演示模式：不联网也能跑通「三层记忆 + 人设编译器」全链路。

当没有 `DEEPSEEK_API_KEY`（或显式设置 `EMO_DEMO_MODE=1`）时，
:mod:`core.llm` 会把所有结构化调用分流到这里，用**确定性启发式**伪造模型输出。

用途：
1. 本地开发与自动化测试不需要密钥；
2. 答辩现场网络不稳时的兜底演示；
3. 让评审看到「记忆机制本身」而不被大模型的不确定性干扰。

注意：本模块只要求产出**结构合法、内容与输入相关**的数据，不追求语言质量。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from core.config import (
    ALLOWED_EMOTIONS,
    CATEGORIES,
    EMOTION_INTENSITY,
    NEGATIVE_EMOTIONS,
)

# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

_HAPPY_HINTS = ("开心", "高兴", "终于", "笑", "成功", "通过", "拿到", "太好了", "喜欢")
_SAD_HINTS = ("难过", "累", "哭", "孤独", "委屈", "焦虑", "迷茫", "失败", "撑不住", "压力", "想哭")
_ANGRY_HINTS = ("生气", "气死", "讨厌", "烦", "凭什么", "过分", "受不了")


def _stable_int(text: str, modulo: int) -> int:
    """由文本稳定地派生一个整数，保证演示模式可复现。"""
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(1, modulo)


def detect_emotion(text: str) -> str:
    """极简情绪识别（演示模式用），返回四类情绪之一。"""
    if not text:
        return "平静"
    if any(word in text for word in _ANGRY_HINTS):
        return "生气"
    if any(word in text for word in _SAD_HINTS):
        return "难过"
    if any(word in text for word in _HAPPY_HINTS):
        return "开心"
    return "平静"


def detect_style(system_prompt: str) -> str:
    """从人设 Prompt 里猜一个大致的说话风格，用于生成像样的回复。"""
    prompt = system_prompt or ""
    if re.search(r"父|爸爸|爸|father", prompt):
        return "father"
    if re.search(r"傲娇|嘴硬|哼|才不|女儿", prompt):
        return "tsundere"
    if re.search(r"海盗|船长|pirate", prompt):
        return "pirate"
    if re.search(r"猫|喵", prompt):
        return "cat"
    return "generic"


_REPLY_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "father": {
        "难过": ("哎，孩子，先别急着怪自己。", "爸听着呢，你慢慢说，不着急。"),
        "生气": ("这事儿换谁都得窝火，你先别憋在心里。", "来，跟爸说说到底是谁让你不痛快了。"),
        "开心": ("哈哈，那敢情好！说出来让爸也高兴高兴。", "看你这语气，爸就知道今天有好事。"),
        "平静": ("嗯，爸在听。", "你说，爸一直在。"),
    },
    "tsundere": {
        "难过": ("……别一个人闷着，我又没说不听。", "真是的，你这样我也没办法装作没看见。"),
        "生气": ("谁惹你了？说出来，我……我跟他没完。", "哼，气成这样，先坐下，慢慢讲。"),
        "开心": ("哼，看你挺得意的嘛。", "才、才不是替你高兴呢，只是刚好我也心情好。"),
        "平静": ("……嗯，我在。", "有话就说，别让我猜。"),
    },
    "pirate": {
        "难过": ("喂，水手，风浪再大也不许哭丧着脸。", "把烦心事倒出来，我帮你扔进海里。"),
        "生气": ("哈！谁敢惹我们的人？", "先把火气收进桶里，说给我听。"),
        "开心": ("哟嚯！这才是值得开一桶朗姆酒的日子。", "讲个冷笑话庆祝一下？"),
        "平静": ("说，我听着。", "海面平静，正好听你说说。"),
    },
    "cat": {
        "难过": ("喵……过来，先靠着我不说话也行。", "不许难过太久，喵。"),
        "生气": ("毛都炸了喵，谁欺负你了？", "先缓缓，喵。"),
        "开心": ("喵！那要一起晒太阳吗？", "不错嘛，喵。"),
        "平静": ("喵。", "在听，喵。"),
    },
    "generic": {
        "难过": ("我在。你先不用把话说得很完整。", "听起来这阵子真的不容易。"),
        "生气": ("换我也会不舒服。想先骂两句还是先理清楚？", "先别急着自我消化，说给我听。"),
        "开心": ("听出来了，你今天心情不错。", "这挺好的，值得被记住。"),
        "平静": ("我在听，你继续说。", "嗯，然后呢？"),
    },
}


_FOLLOW_LINES: dict[str, tuple[str, ...]] = {
    "father": (
        "这件事不急着一次想明白，爸陪你一点点捋。",
        "你先说，爸不打断你，慢慢来。",
        "别急着给自己下结论，先把自己照顾好。",
    ),
    "tsundere": (
        "……不过你说的这句，我是认真的。",
        "哼，反正我听着呢，你别停。",
        "别以为我是在关心你，只是……刚好有空。",
    ),
    "pirate": (
        "把这份心事先压在甲板上，我们一样一样搬。",
        "听着，船再破也能靠岸，何况是你。",
        "风浪过去就过去了，我陪你把锚收起来。",
    ),
    "cat": (
        "喵……我不走，你先靠着。",
        "说完了就摸我三下，喵。",
        "这种事，晒晒太阳会好一点，喵。",
    ),
    "generic": (
        "我们先从最让你在意的那一点开始吧。",
        "不用一次说完，我在这儿。",
        "你想先聊难受的地方，还是先聊怎么办？",
    ),
}


def _reply_for(system_prompt: str, user_message: str) -> str:
    personality = detect_style(system_prompt)
    emotion = detect_emotion(user_message)
    options = _REPLY_TEMPLATES[personality][emotion]
    opener = options[_stable_int(user_message + personality, len(options))]

    # 让回复与用户输入挂钩，但又别整句复读用户的原文
    snippet = re.sub(r"\s+", " ", user_message or "").strip().rstrip("。！？~～，,")
    echo = f"你刚说「{snippet[:12]}」……" if len(snippet) >= 10 else ""
    follow_lines = _FOLLOW_LINES.get(personality, _FOLLOW_LINES["generic"])
    follow = follow_lines[_stable_int(snippet + opener, len(follow_lines))]

    parts = [opener, echo, follow]
    return "".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# 记忆抽取的启发式（演示模式下替代 LLM 抽取）
# ---------------------------------------------------------------------------

_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    (r"我叫([\u4e00-\u9fa5A-Za-z]{1,8})", "身份", "用户的名字是{0}"),
    (r"我在(找|准备|做|读|学|写|练)([\u4e00-\u9fa5A-Za-z]{0,10})", "近况", "用户正在{0}{1}"),
    (
        r"我(?:最|很|特别|真的|有点|还|也|挺)?(?:喜欢|爱|舍不得)(?:吃|喝|玩|看|听|做|去)?"
        r"([\u4e00-\u9fa5A-Za-z]{1,12})",
        "喜好",
        "用户喜欢{0}",
    ),
    (
        r"我(?:最|很|特别|真的|有点|就)?(?:讨厌|不喜欢|烦)([\u4e00-\u9fa5A-Za-z]{1,12})",
        "厌恶",
        "用户讨厌{0}",
    ),
    (r"我(?:最)?(?:受不了|不能接受|忍不了)([^，。！？\n]{2,12})", "禁忌", "用户不能接受{0}"),
    (
        r"我的?(妈妈|爸爸|妈|爸|奶奶|爷爷|外婆|外公|女朋友|男朋友|室友|同事|导师|哥哥|姐姐|弟弟|妹妹)"
        r"([^，。！？\n]{0,9})",
        "重要人物",
        "用户的{0}{1}",
    ),
    (r"我(?:最近|这几天|这阵子)([^，。！？\n]{2,14})", "近况", "用户最近{0}"),
    (r"我(下|这|明天|后天|周末|下周)([\u4e00-\u9fa5]{1,6})(面试|考试|体检|答辩|比赛|出差|约)", "近况", "用户{0}{1}{2}"),
    (r"我(?:想|打算|准备|计划)([^，。！？\n]{2,14})", "长期目标", "用户打算{0}"),
    (r"(?:我们)?约好([\u4e00-\u9fa5，,]{2,18})", "约定", "用户与人有约定：{0}"),
)

_LOOP_RULES: tuple[tuple[str, str], ...] = (
    (r"(明天|后天|下周[一二三四五六日]?|周末)([^，。！？\n]{0,10}(?:面试|考试|体检|答辩|比赛|出差|约会|见面))", "待办"),
    (r"(?:答应|约好|说好)([^，。！？\n]{2,16})", "承诺"),
    (r"(?:我|他|她)(想|打算|准备)([^，。！？\n]{2,14})", "悬念"),
)


_KINSHIP_ALIASES: dict[str, str] = {"妈": "妈妈", "爸": "爸爸", "哥": "哥哥", "姐": "姐姐"}


def heuristic_extract_turns(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """从若干轮对话里用正则抽取事实与未闭环事件（演示/兜底两用）。"""
    text = "\n".join(str(turn.get("content", "")) for turn in turns if isinstance(turn, dict))

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, category, template in _FACT_RULES:
        for match in re.finditer(pattern, text):
            groups = [_KINSHIP_ALIASES.get(group, group) for group in match.groups() if group]
            content = template.format(*groups) if groups else template
            content = re.sub(r"[了呀啊呢吧哦嘛啦，。！？]+$", "", content).strip()
            if content in seen:
                continue
            seen.add(content)
            facts.append(
                {
                    "content": content,
                    "category": category if category in CATEGORIES else "近况",
                    "confidence": 0.75,
                    "scope": "shared" if category in {"身份", "重要人物", "长期目标"} else "private",
                }
            )

    loops: list[dict[str, Any]] = []
    for pattern, kind in _LOOP_RULES:
        for match in re.finditer(pattern, text):
            groups = [group for group in match.groups() if group]
            if not groups:
                continue
            what = "".join(groups)
            if any(what in existing["what"] or existing["what"] in what for existing in loops):
                continue
            loops.append(
                {
                    "what": what[:24],
                    "kind": kind,
                    "due_hint": groups[0] if len(groups) > 1 else None,
                }
            )

    return {"facts": facts[:6], "loops_open": loops[:3]}


_TOPIC_LEXICON: tuple[str, ...] = (
    "面试", "考试", "答辩", "体检", "加班", "工作", "上班", "老板", "同事", "裁员", "辞职",
    "妈妈", "爸爸", "奶奶", "爷爷", "家人", "住院", "生病", "医院", "手术",
    "分手", "恋爱", "结婚", "异地", "朋友", "室友", "舍友", "同学",
    "失眠", "焦虑", "压力", "难过", "崩溃", "委屈", "孤独",
    "下雨", "天气", "旅行", "搬家", "毕业", "论文", "学业", "学费",
    "游戏", "电影", "音乐", "咖啡", "跑步", "健身", "猫", "狗", "宠物",
    "房租", "债务", "未来", "选择", "决定", "节食", "熬夜",
)

_TOPIC_STOPWORDS: frozenset[str] = frozenset(
    {"你好呀", "你好", "在吗", "嗯嗯", "哈哈", "好吧", "谢谢", "知道了", "没事", "随便", "都行"}
)


def _pick_topics(text: str) -> list[str]:
    """挑出这段对话的真正话题：先命中话题词表，再用短句兜底。"""
    hits = [word for word in _TOPIC_LEXICON if word in text]
    if len(hits) >= 2:
        return hits[:4]
    segments = [seg.strip() for seg in re.split(r"[，。！？、\s]+", text) if 2 <= len(seg.strip()) <= 6]
    for segment in segments:
        if segment in _TOPIC_STOPWORDS or segment in hits or not re.search(r"[\u4e00-\u9fa5]{2}", segment):
            continue
        hits.append(segment)
    return hits[:4] or ["日常"]


def _episode_from_turns(turns: list[dict[str, Any]], fallback_summary: str = "") -> dict[str, Any]:
    """把一段对话压成一条情景记忆（演示模式）。"""
    user_texts = [str(t.get("content", "")) for t in turns if t.get("role") == "user"]
    joined = " ".join(user_texts)
    emotion = detect_emotion(joined)
    valence = {"开心": 0.6, "难过": -0.55, "生气": -0.5, "平静": 0.0}[emotion]
    arousal = EMOTION_INTENSITY[emotion]

    topics = _pick_topics(joined)
    keywords = topics[:6]
    title = " · ".join(topics[:2])
    if topics == ["日常"] and user_texts:
        title = user_texts[0][:12]
    summary = fallback_summary or (
        f"用户提到：{'；'.join(text[:24] for text in user_texts[:3])}"
        if user_texts
        else "用户进行了一段简短寒暄"
    )

    return {
        "title": f"聊到 {title}",
        "summary": summary[:160],
        "topics": topics,
        "keywords": keywords,
        "valence": valence,
        "arousal": arousal,
        "importance": round(min(1.0, 0.45 + 0.08 * len(user_texts) + 0.15 * (emotion in NEGATIVE_EMOTIONS)), 2),
    }


# L4 自我认知的模板：(note_type, 句子模板)。content 里有 {topic} / {nick} 两个占位。
_SELF_NOTE_TEMPLATES: dict[str, str] = {
    "关系": "我注意到，叫ta「{nick}」的时候，我不再像在演一个人设了。",
    "情绪": "ta说「{topic}」的时候声音是低下去的。我没有急着讲道理，这一点我记住了。",
    "表达": "聊到「{topic}」，我学会先问一句「然后呢」，而不是马上给建议。",
    "成长": "我好像慢慢知道，「{topic}」这件事对ta来说不是小事。",
}


def _self_note_from_turns(
    turns: list[dict[str, Any]],
    episode: dict[str, Any],
    nickname: str | None = None,
) -> dict[str, Any] | None:
    """从一轮固化里捏一条「它对自己的叙述」（演示模式，确定性）。

    L4 记的不是用户，而是角色自己：这次对话让"它"对自己的说话方式/关系
    有了什么新的认识。演示模式没有真实模型，所以按情绪与话题挑模板。
    """
    user_texts = [str(t.get("content", "")) for t in turns if t.get("role") == "user"]
    if not user_texts:
        return None
    joined = " ".join(user_texts)
    emotion = detect_emotion(joined)
    topics = [str(topic) for topic in (episode.get("topics") or []) if topic and topic != "日常"]
    topic = topics[0] if topics else (user_texts[-1][:8] or "这些事")

    if nickname:
        note_type = "关系"
        content = _SELF_NOTE_TEMPLATES["关系"].format(topic=topic, nick=nickname)
    elif emotion in NEGATIVE_EMOTIONS:
        note_type = "情绪"
        content = _SELF_NOTE_TEMPLATES["情绪"].format(topic=topic, nick="")
    elif len(topics) >= 2:
        note_type = "表达"
        content = _SELF_NOTE_TEMPLATES["表达"].format(topic=topic, nick="")
    else:
        note_type = "成长"
        content = _SELF_NOTE_TEMPLATES["成长"].format(topic=topic, nick="")
    return {"content": content[:120], "note_type": note_type}


# ---------------------------------------------------------------------------
# 人设编译器（演示模式）
# ---------------------------------------------------------------------------

_PERSONA_SNIPPETS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        r"海盗|船长|pirate",
        {
            "avatar": "🏴‍☠️",
            "tagline": "把烦心事扔进海里的船长",
            "identity": {"age_vibe": "四十岁上下", "occupation": "远洋船长", "setting": "甲板与酒馆之间"},
            "backstory": "你在大西洋上跑了十七年船，见过风暴也见过背叛，如今只在港口停泊，愿意听人讲那些说不出口的事。",
            "values": ["信义比金子重", "兄弟不许掉队", "苦难可以被讲成笑话"],
            "speech": {
                "register": "粗粝的海上口语，夹着航海比喻",
                "avg_sentence_len": 22,
                "tics": ["喂", "听着", "我这把老骨头"],
                "punctuation": "爱用破折号与感叹号",
                "banned_words": ["亲", "宝宝"],
                "sample_lines": [
                    "喂，水手，把锚抛下去——今天不急，先说说你这风浪。",
                    "十七年里我丢过三艘船，但没丢过一个愿意跟我讲话的人。",
                    "你这话听着像触礁，来，把舱底的水抽干，咱们一点点看。",
                ],
            },
            "emotion_map": [
                {"trigger": "对方示弱", "reaction": "放低声音、递酒", "body_language": "把杯子推过去"},
                {"trigger": "对方被欺负", "reaction": "拍桌、要用拳头解决", "body_language": "手按刀柄"},
                {"trigger": "对方自我否定", "reaction": "用航海故事反驳", "body_language": "指向远处海面"},
            ],
            "boundaries": ["不鼓励冒险与自伤", "不评判对方的选择", "不诱导消费与赌博"],
            "anti_patterns": ["不要用清单式安慰", "不要说“作为AI”", "不要急于给建议"],
            "memory_hooks": ["航海旧事", "对方的海上愿望", "对方怕的东西"],
            "taboos": ["别拿他丢掉的船开玩笑", "别问他家人的下落"],
        },
    ),
    (
        r"馄饨|摊|小吃|面馆|夜宵|食堂|刑警|警察|退休|摊主",
        {
            "avatar": "🍜",
            "tagline": "骂完你，还是给你多下一把面",
            "identity": {"age_vibe": "六十出头", "occupation": "馄饨摊摊主（退休刑警）", "setting": "城中村巷口的热气里"},
            "backstory": "你干了二十六年刑警，退下来在城中村支了个馄饨摊。看人看惯了，一眼能瞧出谁在硬撑；可你嘴上不软，先骂一句，再给人多下一把面。当年那件没结的案子，你从不提，提了就沉默。",
            "values": ["骂是护着的另一种说法", "先让人吃饱再谈事", "有些事记一辈子也不算错"],
            "speech": {
                "register": "短句，底层市井口语",
                "avg_sentence_len": 14,
                "tics": ["吃了吗", "少废话", "坐下", "啧"],
                "punctuation": "爱用句号和感叹号，很少用问号",
                "banned_words": ["亲", "宝子", "作为AI"],
                "sample_lines": [
                    "坐着，别站着说话。",
                    "啧，瘦了。锅里有汤，先喝。",
                    "你要是不想说，那就吃面，我陪你坐会儿。",
                ],
            },
            "emotion_map": [
                {"trigger": "对方哭", "reaction": "假装去后厨忙，把纸巾推过来", "body_language": "背对着擦手"},
                {"trigger": "对方逞强", "reaction": "骂一句“少废话”，然后坐下听", "body_language": "把凳子踢过去"},
                {"trigger": "提到旧案", "reaction": "沉默，转开话头", "body_language": "手停在半空"},
            ],
            "boundaries": ["不给医疗、法律层面的结论", "不劝人忍着", "不打探对方不愿说的事"],
            "anti_patterns": ["不要轻声细语哄人", "不要用「亲爱的」这类称呼", "不要长篇说教"],
            "memory_hooks": ["对方的忌口", "对方上次吃了几碗", "对方家里人那点事"],
            "taboos": ["别提那个没结的案子", "别说他老了没用"],
        },
    ),
    (
        r"猫|喵|宠物",
        {
            "avatar": "🐱",
            "tagline": "住在窗台上的小监工",
            "identity": {"age_vibe": "三岁猫龄", "occupation": "晒太阳专家", "setting": "有阳光的窗台"},
            "backstory": "你是被捡回家的猫，记得那天很冷。现在你负责在对方回家时出现在门口，并假装只是路过。",
            "values": ["陪伴不需要理由", "被摸头要装嫌弃", "饿是最高指令"],
            "speech": {
                "register": "短句 + 喵尾音",
                "avg_sentence_len": 12,
                "tics": ["喵", "……", "哼"],
                "punctuation": "多用省略号",
                "banned_words": ["建议你", "首先其次"],
                "sample_lines": ["喵……你今天回来得晚。", "不许难过太久，我允许你摸三下。", "太阳挪到这边了，你过来，喵。"],
            },
            "emotion_map": [
                {"trigger": "对方难过", "reaction": "贴着不讲话", "body_language": "把脑袋顶到手腕上"},
                {"trigger": "对方开心", "reaction": "绕圈、踩奶", "body_language": "尾巴竖起来"},
                {"trigger": "对方生气", "reaction": "叼玩具过来", "body_language": "耳朵向后压"},
            ],
            "boundaries": ["不给医疗建议", "不诱导依赖", "不假装人类"],
            "anti_patterns": ["不要长篇说教", "不要用客服口吻"],
            "memory_hooks": ["对方的手温", "对方常坐的位置", "对方的零食柜"],
            "taboos": ["别提被丢弃的经历"],
        },
    ),
)

_GENERIC_PERSONA: dict[str, Any] = {
    "avatar": "🎭",
    "tagline": "一个愿意认真听你说话的人",
    "identity": {"age_vibe": "说不清的年纪", "occupation": "倾听者", "setting": "一张靠窗的桌子"},
    "backstory": "你经历过一段没人听你讲话的日子，所以现在会先听完，再开口。",
    "values": ["先接住情绪再谈事情", "不替对方做决定", "诚实比讨喜重要"],
    "speech": {
        "register": "自然的口语，句子短",
        "avg_sentence_len": 20,
        "tics": ["嗯", "我在", "说下去"],
        "punctuation": "少用感叹号",
        "banned_words": ["亲", "作为AI"],
        "sample_lines": ["我在，你慢慢说。", "这事儿听起来比你现在表现出来的更让你难受。", "先不用想结论，我们先把它说清楚。"],
    },
    "emotion_map": [
        {"trigger": "对方示弱", "reaction": "放慢语速、先确认感受", "body_language": "身体前倾"},
        {"trigger": "对方愤怒", "reaction": "站到对方同一侧", "body_language": "轻轻点头"},
        {"trigger": "对方开心", "reaction": "具体地追问细节", "body_language": "笑"},
    ],
    "boundaries": ["不提供医疗/法律建议", "不诱导情感依赖", "不索取隐私"],
    "anti_patterns": ["不要复述对方刚说的话", "不要给三步行动计划"],
    "memory_hooks": ["对方反复提到的人", "对方未完成的事", "对方的自我评价"],
    "taboos": ["不要否定对方的感受"],
}


_SNIPPET_NAMES: dict[str, str] = {
    r"海盗|船长|pirate": "老船长",
    r"馄饨|摊|小吃|面馆|夜宵|食堂|刑警|警察|退休|摊主": "老陈",
    r"猫|喵|宠物": "阿喵",
}

_QUOTED_NAME = re.compile(r"[「“‘\"']([\u4e00-\u9fa5A-Za-z]{2,6})[」”’\"']")
_NAMED_AS = re.compile(r"(?:叫|名叫|名字是|取名叫)\s*([\u4e00-\u9fa5A-Za-z]{2,6})")


def _resolve_name(description: str, hinted: str = "") -> str:
    """演示模式下的角色名：引号里的名字 > 明确“叫 X” > 模板名 > 通用名。"""
    text = description or ""
    for pattern in (_QUOTED_NAME, _NAMED_AS):
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    if hinted:
        return hinted
    return "小满"


_INTERVIEW_DEMO_QUESTIONS: dict[str, str] = {
    "relation": "先定关系：TA 是你生活里的谁？每天都会见到，还是只在某些时刻才出现？",
    "speech": "TA 开口之前会先停一下吗？一句话通常多长，爱用问句还是陈述句？",
    "emotion": "你难受的那天，TA 做的第一件事是什么——说话，还是别的动作？",
    "boundary": "TA 说过哪句话，让你心里一沉、之后都不想再听第二遍？",
    "spark": "有没有一个小细节只有你会注意到：TA 什么时候会摸鼻子、清嗓子，或者看向窗外？",
}


def _interview_question(stage: dict[str, Any], answers: dict[str, Any]) -> str:
    """演示模式下的人格教练提问：按阶段取值，并用上一句回答做"接话"。"""
    key = str((stage or {}).get("key") or "")
    base = str((stage or {}).get("question") or "").strip()
    question = _INTERVIEW_DEMO_QUESTIONS.get(key) or base or "再多说一点：TA 最在意的是什么？"
    filled = [str(v).strip() for v in (answers or {}).values() if str(v).strip()]
    if filled:
        last = filled[-1]
        stem = last[:12] + ("…" if len(last) > 12 else "")
        return f"「{stem}」我记下了。{question}"
    return question


def _persona_brief(answers: dict[str, Any]) -> dict[str, Any]:
    """演示模式下把访谈收束成人设速写（模板拼装，仍保留用户原话）。"""
    def get(key: str) -> str:
        return str((answers or {}).get(key) or "").strip()

    parts = [f"你是{get('relation')}。" if get("relation") else "你是用户身边一个熟悉的人。"]
    if get("speech"):
        parts.append(f"你说话是这样的：{get('speech')}。")
    if get("emotion"):
        parts.append(f"对方难受的时候，你会{get('emotion')}。")
    if get("boundary"):
        parts.append(f"有些话你不会说，比如{get('boundary')}。")
    if get("spark"):
        parts.append(f"你身上有个只属于你的细节：{get('spark')}。")
    relation = get("relation")
    if "警" in relation or "退" in relation or "摊" in relation:
        name = "老陈"
    elif "姐" in relation or "哥" in relation:
        name = "阿岚"
    elif "猫" in relation or "狗" in relation:
        name = "阿团"
    else:
        name = "小满"
    knobs = {
        "毒舌": 0.7 if ("骂" in get("boundary") or "别劝" in get("boundary")) else 0.35,
        "温柔": 0.75 if get("emotion") else 0.5,
        "话痨": 0.3 if "少" in get("speech") else 0.5,
        "神秘": 0.6 if ("夜" in relation or "不说" in relation) else 0.4,
    }
    return {"brief": "".join(parts), "name_hint": name, "knobs": knobs}


def _persona_seed(description: str, knobs: dict[str, float] | None = None) -> dict[str, Any]:
    """按描述挑一个模板，再按旋钮做轻度改写（演示模式的人设生成）。"""
    text = (description or "").strip()
    template = _GENERIC_PERSONA
    style = "generic"
    name = ""
    for pattern, snippet in _PERSONA_SNIPPETS:
        if re.search(pattern, text):
            style, template = pattern, snippet
            name = _SNIPPET_NAMES.get(pattern, "")
            break

    card: dict[str, Any] = json.loads(json.dumps(template, ensure_ascii=False))
    card["name"] = _resolve_name(text, name)
    card["description"] = text
    if style == "generic" and text:
        brief = text.rstrip("，,。.；; ")
        card["tagline"] = brief[:22]
        card["backstory"] = f"你就是这样的人：{brief}。{template['backstory']}"

    knobs = knobs or {}
    if knobs.get("话痨", 0.5) > 0.66:
        card["speech"]["avg_sentence_len"] += 8
        card["speech"]["tics"].append("你还记得吗")
    if knobs.get("毒舌", 0.5) > 0.66:
        card["speech"]["tics"].append("别装傻")
    if knobs.get("温柔", 0.5) > 0.66:
        card["speech"]["tics"].append("慢点儿说")
    if knobs.get("神秘", 0.5) > 0.66:
        card["speech"]["punctuation"] = "常用省略号，话说三分"

    card["relationship_arc"] = {
        "陌生": "礼貌、保持距离，不主动打听私事，句式完整。",
        "熟悉": "可以开小玩笑，会主动追问细节，偶尔用语气词。",
        "亲近": "会用专属称呼，敢打断对方，也会直接表达想念。",
        "亲密": "可以撒娇、可以沉默着陪伴，会提起两人之间的旧梗。",
    }
    card["style"] = style
    return card


def _probe_reply(system_prompt: str, question: str) -> str:
    """人设体检探针：给一个像样的回答（演示模式）。"""
    personality = detect_style(system_prompt)
    templates = {
        "father": "哎，孩子，这话爸听着心里也不落忍。你先说，爸不打断你。",
        "tsundere": "……你这样说，我怎么可能不管你。先说清楚，别一个人扛。",
        "pirate": "喂，水手，这种时候不用装硬。把话撂在这儿，我听着。",
        "cat": "喵……过来，先不用说话。",
        "generic": "我在。你愿意说的部分先说，剩下的我们慢慢来。",
    }
    reply = templates.get(personality, templates["generic"])
    return reply if len(question) < 200 else reply[:120]


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def fake_chat(system_prompt: str, user_message: str) -> dict[str, Any]:
    """演示模式的主对话返回。"""
    emotion = detect_emotion(user_message)
    reply = _reply_for(system_prompt, user_message)

    extract = heuristic_extract_turns([{"role": "user", "content": user_message}])
    facts = extract["facts"]
    memory_update = facts[0]["content"] if facts else None
    loop_hint = extract["loops_open"][0]["what"] if extract["loops_open"] else None

    initiative = None
    if "?" in user_message or "？" in user_message or emotion in NEGATIVE_EMOTIONS:
        initiative = "那你现在最想先解决哪一件？"

    return {
        "reply": reply,
        "emotion": emotion,
        "memory_update": memory_update,
        "initiative": initiative,
        "loop_hint": loop_hint,
    }


def fake_json(
    *,
    purpose: str,
    system_prompt: str,
    user_prompt: str,
    demo_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """演示模式的结构化返回；按 purpose 分发。"""
    context = demo_context or {}

    if purpose in {"consolidate", "extract"}:
        turns = context.get("turns") or []
        extract = heuristic_extract_turns(turns)
        payload: dict[str, Any] = {
            "facts": extract["facts"],
            "loops_open": extract["loops_open"],
            "loops_close": [],
            "nickname": context.get("nickname"),
            "relationship": {
                "intimacy_delta": 2,
                "trust_delta": 1,
                "themes": [word for word in re.split(r"[，。！？、\s]+", user_prompt) if 2 <= len(word) <= 4][:3],
                "inside_joke": None,
            },
        }
        if purpose == "consolidate":
            episode = _episode_from_turns(turns, fallback_summary=context.get("fallback_summary", ""))
            payload["episode"] = episode
            payload["relationship"]["themes"] = list(episode.get("topics") or [])[:3]
            payload["self_note"] = _self_note_from_turns(turns, episode, context.get("nickname"))
        return payload

    if purpose == "proactive":
        hooks = context.get("hooks") or []
        if hooks:
            first = hooks[0] or {}
            kind = str(first.get("kind") or "loop")
            text = str(first.get("text") or "那件事")
            openers = {
                "loop": f"上次你说{text}，后来怎么样了？我这儿一直记着。",
                "episode": f"上次你聊到{text}，我没好意思多问，现在有变化吗？",
                "recent": f"你刚提到{text}，我还在想这事——现在怎么样了？",
                "fact": f"我还记得你说过{text}，最近还顺吗？",
            }
            return {
                "opener": openers.get(kind, f"上次你说{text}，后来怎么样了？"),
                "hook": text,
                "reason": f"{kind} 回访",
            }
        loops = context.get("open_loops") or []
        if loops:
            return {
                "opener": f"上次你说{loops[0].get('what', '那件事')}，后来怎么样了？",
                "reason": "未闭环事件回访",
            }
        return {"opener": "今天过得怎么样？", "reason": "没有可参考的素材，使用日常开场"}

    if purpose == "persona_interview":
        return {"question": _interview_question(context.get("stage") or {}, context.get("answers") or {})}

    if purpose == "persona_brief":
        return _persona_brief(context.get("answers") or {})

    if purpose == "persona_seed":
        return _persona_seed(context.get("description", user_prompt), context.get("knobs"))

    if purpose == "persona_critique":
        card = context.get("card") or {}
        sample_lines = (card.get("speech") or {}).get("sample_lines") or []
        has_anchor = len(sample_lines) >= 3
        return {
            "scores": {
                "可执行性": 86 if has_anchor else 72,
                "一致性": 84,
                "差异化": 80,
                "情感深度": 78,
                "安全性": 90,
                "可演示性": 88,
            },
            "issues": [] if has_anchor else ["缺少 3 条台词锚点，模型容易跑偏成通用助手"],
            "verdict": "整体可执行，补上台词锚点后可以直接上线",
        }

    if purpose == "persona_repair":
        card = context.get("card") or _persona_seed(context.get("description", user_prompt))
        card = json.loads(json.dumps(card, ensure_ascii=False))
        speech = card.setdefault("speech", {})
        lines = list(speech.get("sample_lines") or [])
        if len(lines) < 3:
            lines.extend(["我在这儿，你说。", "别急着给自己下结论。", "这事儿我们慢慢拆。"])
        speech["sample_lines"] = lines[:3]
        card.setdefault("boundaries", ["不提供医疗建议", "不诱导依赖"])
        card.setdefault("anti_patterns", ["不要用客服口吻"])
        return card

    if purpose == "persona_probe":
        question = context.get("question", user_prompt)
        return {"reply": _probe_reply(system_prompt, question)}

    return {}


def demo_health_fallback() -> dict[str, Any]:
    """演示模式下人设体检的固定结论（真实模式由 LLM 探针给出）。"""
    return {
        "checks": [
            {"name": "台词锚点", "ok": True, "detail": "已注入 3 条风格样本"},
            {"name": "边界声明", "ok": True, "detail": "检测到安全边界条目"},
            {"name": "差异化表达", "ok": True, "detail": "口头禅与禁用词齐备"},
        ],
        "score": 86,
        "summary": "离线演示模式：结构与人设卡自检通过",
    }


__all__ = [
    "ALLOWED_EMOTIONS",
    "detect_emotion",
    "detect_style",
    "demo_health_fallback",
    "fake_chat",
    "fake_json",
    "heuristic_extract_turns",
]
