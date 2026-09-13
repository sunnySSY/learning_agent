"""复习卡片：把内容拆成问答对，存成本地 JSON。

存储位置由 .env 的 FLASHCARD_DIR 决定，默认 ./data/flashcards/<主题>.json。
同一主题多次生成会追加，不会覆盖已有卡片。
"""

import json
import re
from datetime import datetime

from config import cfg
from llm import chat_model

FLASHCARD_DESC = (
    "把指定内容整理成复习卡片（问答对）并保存到本地文件。"
    "当用户说「做几张复习卡片」「帮我出题」「整理成卡片」「方便我背」时使用。"
    "topic 是主题名，会用作文件名；content 是要整理的内容；count 是卡片数量（1-20）。"
)

_PROMPT = """把下面的内容整理成 {count} 张复习卡片。

严格输出 JSON 数组，不要有任何解释文字，也不要用 Markdown 代码块包裹：
[{{"question": "问题", "answer": "答案"}}]

要求：
- 问题要能独立看懂，不要出现「上文」「这段」「该资料」这类指代
- 答案简短准确，只依据给定内容，不要补充内容里没有的东西
- 覆盖不同的要点，不要几张卡片问同一件事

主题：{topic}

内容：
{content}"""

# 卡片数量上限，防止一次生成几百张把上下文撑爆
_MAX_CARDS = 20


def _strip_fence(text: str) -> str:
    """模型常把 JSON 包在 ``` 里，剥掉。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_name(topic: str) -> str:
    """把主题名变成安全的文件名。"""
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", (topic or "").strip())
    return name[:40] or "cards"


def make_flashcards(topic: str, content: str, count: int = 5) -> str:
    """生成复习卡片并保存。"""
    topic = (topic or "").strip()
    content = (content or "").strip()
    if not topic:
        raise ValueError("主题为空")
    if not content:
        raise ValueError("内容为空，没有可整理的材料")

    count = max(1, min(int(count), _MAX_CARDS))

    response = chat_model().invoke(
        _PROMPT.format(count=count, topic=topic, content=content[:6000])
    )
    raw = _strip_fence(response.content if isinstance(response.content, str) else str(response.content))

    try:
        cards = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型没有返回合法 JSON：{exc}") from exc

    if not isinstance(cards, list) or not cards:
        raise ValueError("模型返回的不是非空数组")

    cleaned = []
    now = datetime.now().isoformat(timespec="seconds")
    for item in cards:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            cleaned.append({"question": question, "answer": answer, "created_at": now})

    if not cleaned:
        raise ValueError("模型返回的卡片缺少 question 或 answer 字段")

    # 与同主题的旧卡片合并
    cfg.flashcard_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.flashcard_dir / f"{_safe_name(topic)}.json"
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data.get("cards", [])
        except (json.JSONDecodeError, OSError):
            existing = []

    all_cards = existing + cleaned
    path.write_text(
        json.dumps(
            {"topic": topic, "updated_at": now, "cards": all_cards},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    preview = "\n".join(f"  Q: {c['question']}\n  A: {c['answer']}" for c in cleaned[:3])
    return (
        f"已生成 {len(cleaned)} 张「{topic}」的复习卡片，保存到 {path}"
        f"（该主题累计 {len(all_cards)} 张）。\n前几张预览：\n{preview}"
    )
