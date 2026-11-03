"""情景化情感对话智能体：DeepSeek 结构化 JSON 版本。

核心流程：
1. 根据角色拼接 System Prompt，并注入该角色的长期记忆。
2. 调用 DeepSeek 的 OpenAI 兼容接口，强制返回 JSON。
3. 解析 reply / emotion / memory_update，分别更新聊天界面、session_state 和本地 JSON。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import streamlit as st
from openai import OpenAI

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
MEMORY_FILE = DATA_DIR / "memory.json"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
MAX_CONTEXT_MESSAGES = 20

ALLOWED_EMOTIONS = ("开心", "难过", "生气", "平静")
EMOTION_ALIASES = {
    "高兴": "开心",
    "快乐": "开心",
    "愉快": "开心",
    "伤心": "难过",
    "悲伤": "难过",
    "失落": "难过",
    "愤怒": "生气",
    "恼火": "生气",
    "中性": "平静",
    "平淡": "平静",
}

USER_AVATAR = "🙂"

ROLE_CONFIG: dict[str, dict[str, str]] = {
    "温柔老父亲": {
        "avatar": "👨",
        "opening": "孩子，今天过得怎么样？坐下来慢慢说，爸爸听着呢。",
        "persona": (
            "温柔、包容、有生活智慧的老父亲，说话慢条斯理，"
            "喜欢用“哎”或“哈哈”开头。无论用户说什么，都先接纳他的情绪，"
            "不评判、不指责、不急着说教；关心要具体，可以称呼用户为“孩子”。"
        ),
    },
    "傲娇女儿": {
        "avatar": "👧",
        "opening": "哼，你终于来了。有话快说，我才没有一直在等你呢。",
        "persona": (
            "嘴硬心软的傲娇女儿，表面上有点不耐烦、爱逞强，实际上很在意用户，"
            "会认真听用户说话。语气自然、俏皮，可以偶尔嘴硬，"
            "但绝不能刻薄、贬低或攻击用户。"
        ),
    },
}

JSON_OUTPUT_RULES = """
你必须严格输出合法的 JSON 格式，不要输出任何额外的解释文字、Markdown 代码围栏或前后缀。
JSON 对象必须包含且只包含以下三个字段：
{
  "reply": "你的回复内容，以当前角色口吻说 2 到 4 句话",
  "emotion": "用户当前的情绪，从 [开心, 难过, 生气, 平静] 中选一个",
  "memory_update": "如果用户透露了新的、值得长期记住的个人信息，请总结为一句话；否则输出 null"
}

字段规则：
1. reply：只放给用户看的具体回复，不要在这里分析情绪或解释 JSON。
2. emotion：指“用户当前的情绪”，不是你所扮演角色的情绪，只能从开心、难过、生气、平静中选一个。
3. memory_update：只记录用户明确说出的稳定信息，例如姓名、称呼、喜好、厌恶、重要人物、重要事件、长期目标；不要记录一次性的情绪、天气或寒暄，也不要猜测。没有这类信息时必须返回 null。
4. 记忆只是背景信息，不要生硬复述，也不要刻意提及“这是记忆”。如果记忆与用户当前说法冲突，以用户当前说法为准。
5. 无论用户说什么，都必须保持角色人设，并且只输出 JSON。

JSON 输出示例：
{
  "reply": "哎，孩子，听你这么说，爸爸心里也跟着沉了一下。先别急着逼自己，慢慢来，爸爸陪着你。",
  "emotion": "难过",
  "memory_update": null
}
"""

st.set_page_config(
    page_title="情景化情感对话智能体",
    page_icon="💬",
    layout="centered",
)


# ---------------------------------------------------------------------------
# 本地 JSON：聊天记录 + 长期记忆
# ---------------------------------------------------------------------------


def default_store() -> dict[str, Any]:
    return {"version": 2, "chats": {}, "memories": {}}


def _normalize_messages(raw_messages: Any) -> list[dict[str, str]]:
    if not isinstance(raw_messages, list):
        return []

    return [
        message
        for message in raw_messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]


def _normalize_memories(raw_memories: Any) -> list[str]:
    if not isinstance(raw_memories, list):
        return []

    memories: list[str] = []
    for item in raw_memories:
        if isinstance(item, str) and item.strip():
            memories.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("content"), str):
            if item["content"].strip():
                memories.append(item["content"].strip())

    # 去重，同时保留原有顺序。
    return list(dict.fromkeys(memories))


def load_store() -> dict[str, Any]:
    """读取本地数据，并兼容上一版“角色名 -> 消息列表”的旧格式。"""
    if not MEMORY_FILE.exists():
        return default_store()

    try:
        raw: Any = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_store()

    if not isinstance(raw, dict):
        return default_store()

    # 新格式
    if "chats" in raw or "memories" in raw:
        raw_chats = raw.get("chats", {})
        raw_memories = raw.get("memories", {})
        chats = {
            role: _normalize_messages(messages)
            for role, messages in raw_chats.items()
            if isinstance(role, str)
        } if isinstance(raw_chats, dict) else {}
        memories = {
            role: _normalize_memories(items)
            for role, items in raw_memories.items()
            if isinstance(role, str)
        } if isinstance(raw_memories, dict) else {}
        return {"version": 2, "chats": chats, "memories": memories}

    # 旧格式迁移：{"温柔老父亲": [{"role": "...", "content": "..."}]}
    chats = {
        role: _normalize_messages(messages)
        for role, messages in raw.items()
        if isinstance(role, str)
    }
    return {"version": 2, "chats": chats, "memories": {}}


def save_store(store: dict[str, Any]) -> None:
    """原子写入 JSON，尽量避免程序中断时留下半个文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "chats": store.get("chats", {}),
        "memories": store.get("memories", {}),
    }
    temp_file = MEMORY_FILE.with_suffix(".json.tmp")
    temp_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(MEMORY_FILE)


def build_opening_message(role: str) -> dict[str, str]:
    return {"role": "assistant", "content": ROLE_CONFIG[role]["opening"]}


def load_role_messages(store: dict[str, Any], role: str) -> list[dict[str, str]]:
    messages = store["chats"].get(role, [])
    return messages if messages else [build_opening_message(role)]


def load_role_memories(role: str) -> list[str]:
    """每次调用都重新读取 memory.json，确保 Prompt 使用的是最新用户画像。"""
    return load_store()["memories"].get(role, [])


# ---------------------------------------------------------------------------
# Prompt 与 DeepSeek 调用
# ---------------------------------------------------------------------------


def build_system_prompt(role: str, memories: list[str]) -> str:
    memory_json_content = json.dumps(memories, ensure_ascii=False, indent=2)
    return (
        f"你现在扮演【{role}】。\n"
        f"角色设定：{ROLE_CONFIG[role]['persona']}\n\n"
        "【记忆背景】\n"
        "以下是用户之前告诉过你的事情，请在对话中自然地体现出来，不要刻意提及这是记忆：\n"
        f"{memory_json_content}\n"
        "如果记忆数组为空，说明你还不了解用户，不要编造记忆。\n\n"
        "【输出要求】\n"
        f"{JSON_OUTPUT_RULES}"
    )


@st.cache_resource(show_spinner=False)
def get_deepseek_client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=60.0,
        max_retries=1,
    )


def parse_structured_reply(content: Optional[str]) -> dict[str, Any]:
    """解析并校验模型返回的 JSON，必要时兼容 Markdown 代码围栏。"""
    if not content or not content.strip():
        raise ValueError("模型返回了空内容")

    text = content.strip()

    # 虽然已经启用 JSON Output，仍兼容模型偶尔包上 ```json 的情况。
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("模型返回的内容不是合法 JSON")
        data = json.loads(text[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("模型返回的 JSON 顶层必须是对象")

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("JSON 缺少有效的 reply 字段")

    raw_emotion = data.get("emotion")
    emotion = str(raw_emotion).strip() if raw_emotion is not None else "平静"
    emotion = EMOTION_ALIASES.get(emotion, emotion)
    if emotion not in ALLOWED_EMOTIONS:
        emotion = "平静"

    raw_memory = data.get("memory_update")
    if raw_memory is None:
        memory_update = None
    elif isinstance(raw_memory, str):
        memory_update = raw_memory.strip()
        if memory_update.lower() in {"", "null", "none", "无", "没有"}:
            memory_update = None
    else:
        # 模型没有严格遵守字符串格式时，保留信息而不是直接丢弃。
        memory_update = json.dumps(raw_memory, ensure_ascii=False)

    return {
        "reply": reply.strip(),
        "emotion": emotion,
        "memory_update": memory_update,
    }


def request_structured_reply(
    role: str,
    messages: list[dict[str, str]],
    memories: list[str],
) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")

    client = get_deepseek_client(api_key)
    request_messages: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(role, memories)}
    ]
    request_messages.extend(
        {"role": message["role"], "content": message["content"]}
        for message in messages[-MAX_CONTEXT_MESSAGES:]
    )

    last_error: Optional[Exception] = None
    # 重要：deepseek-flash 默认开启思考模式，会消耗 max_tokens 并增加延迟。
    # 本场景只需要短回复和 JSON，关闭思考模式可以避免空内容/截断，响应也更快。
    # 前两次使用 JSON Mode；若仍返回空内容，第三次退回严格提示词模式，由本地解析器兜底。
    for attempt in range(3):
        create_kwargs: dict[str, Any] = {
            "model": DEEPSEEK_MODEL,
            "messages": request_messages,
            "temperature": 0.8,
            "max_tokens": 1200,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if attempt < 2:
            create_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**create_kwargs)
        if not response.choices:
            last_error = ValueError("模型没有返回候选结果")
            continue

        choice = response.choices[0]
        if choice.finish_reason == "length":
            last_error = ValueError("模型输出被截断")
            continue

        content = choice.message.content
        if not content or not content.strip():
            last_error = ValueError(
                f"模型返回空内容（finish_reason={choice.finish_reason}）"
            )
            continue

        try:
            return parse_structured_reply(content)
        except ValueError as exc:
            last_error = exc

    raise RuntimeError(f"无法解析模型返回的 JSON：{last_error}")


def add_memory(store: dict[str, Any], role: str, memory_update: Optional[str]) -> bool:
    """保存一条长期记忆；完全重复的内容不会重复写入。"""
    if not memory_update:
        return False

    memories = store["memories"].setdefault(role, [])
    normalized = " ".join(memory_update.split())
    if any(" ".join(existing.split()) == normalized for existing in memories):
        return False

    memories.append(memory_update)
    return True


# ---------------------------------------------------------------------------
# 初始化 session_state
# ---------------------------------------------------------------------------

if "emotion" not in st.session_state:
    st.session_state.emotion = "平静"

api_key_configured = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# 页面：侧边栏
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🎭 角色设置")
    selected_role = st.selectbox("选择对话角色", options=list(ROLE_CONFIG))
    st.caption(f"模型：`{DEEPSEEK_MODEL}` · JSON 结构化输出")
    st.divider()

    role_memories = load_role_memories(selected_role)
    with st.expander("TA 对你的印象", expanded=False):
        st.caption(f"实时读取自 `data/memory.json` · 共 {len(role_memories)} 条")
        if role_memories:
            for index, memory in enumerate(role_memories, start=1):
                st.markdown(f"**{index}.** {memory}")
        else:
            st.caption("TA 还没有记住关于你的重要信息，聊久一点试试。")

    st.divider()

    if st.button("清空当前角色数据", use_container_width=True):
        store = load_store()
        store["chats"].pop(selected_role, None)
        store["memories"].pop(selected_role, None)
        save_store(store)

        st.session_state.current_role = selected_role
        st.session_state.messages = [build_opening_message(selected_role)]
        st.session_state.emotion = "平静"
        st.rerun()

    st.caption(f"记忆文件：`{MEMORY_FILE}`")

    if api_key_configured:
        st.success("已检测到 DEEPSEEK_API_KEY", icon="✅")
    else:
        st.error("未检测到 DEEPSEEK_API_KEY", icon="🔑")
        st.caption("请先在启动 Streamlit 的终端中设置环境变量，然后重启应用。")


# 切换角色时，从 JSON 中加载该角色自己的对话与情绪初始值。
if st.session_state.get("current_role") != selected_role:
    st.session_state.current_role = selected_role
    st.session_state.messages = load_role_messages(load_store(), selected_role)
    st.session_state.emotion = "平静"


# ---------------------------------------------------------------------------
# 页面：聊天区域
# ---------------------------------------------------------------------------

role_info = ROLE_CONFIG[selected_role]

st.title("💬 情景化情感对话智能体")
st.caption(
    f"正在与「{selected_role}」对话 · 感知情绪：{st.session_state.emotion}"
)

for message in st.session_state.messages:
    avatar = USER_AVATAR if message["role"] == "user" else role_info["avatar"]
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])

prompt = st.chat_input(
    f"对「{selected_role}」说点什么……",
    disabled=not api_key_configured,
)

if prompt:
    # 1. 在调用模型之前，从 memory.json 读取该角色最新的用户画像。
    store = load_store()
    role_memories = store["memories"].get(selected_role, [])

    # 2. 先把用户消息加入当前会话并落盘，避免模型调用失败时丢失输入。
    user_message = {"role": "user", "content": prompt}
    st.session_state.messages.append(user_message)
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    store["chats"][selected_role] = st.session_state.messages
    save_store(store)

    with st.spinner(f"{selected_role}正在认真听你说……"):
        try:
            # 3. 将最新记忆作为 Context 注入 System Prompt。
            result = request_structured_reply(
                role=selected_role,
                messages=st.session_state.messages,
                memories=role_memories,
            )
        except Exception as exc:
            st.error(f"调用 DeepSeek 失败：{exc}")
            st.caption("请检查 API Key、网络连接和模型名称后重试。")
        else:
            assistant_message = {"role": "assistant", "content": result["reply"]}
            st.session_state.messages.append(assistant_message)
            st.session_state.emotion = result["emotion"]

            store["chats"][selected_role] = st.session_state.messages
            memory_added = add_memory(
                store,
                selected_role,
                result["memory_update"],
            )
            save_store(store)

            if memory_added:
                st.toast("已记住一条新的重要信息", icon="🧠")

            # 重新运行，让侧边栏记忆列表和顶部情绪同步刷新。
            st.rerun()
