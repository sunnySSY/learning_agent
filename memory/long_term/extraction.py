"""长期记忆提取器。

P0 使用有来源的显式表达规则，避免把助手回答或学科资料误存为用户事实。
后续可替换为结构化 LLM 提取器，但必须继续经过本模块的字段和来源校验。
"""

import re
from uuid import uuid4

from .schemas import MemoryCandidate


_SENSITIVE = re.compile(r"(密码|密钥|api[_ -]?key|身份证|银行卡|住址|电话|手机号)", re.I)
_GOAL = re.compile(r"(?:我(?:的)?(?:学习目标|目标|准备|正在准备|要准备)(?:是|为)?|准备)([^，。；;\n]{2,80})")
_PREFERENCE = re.compile(r"(?:我(?:希望|喜欢|偏好|想要)|以后|请)([^，。；;\n]{2,100})")
_TIME = re.compile(r"(?:每天|每日)\s*(\d{1,3})\s*(?:分钟|分|min)")
_WEAK = re.compile(r"(?:我(?:不懂|不会|不太会|搞不清|总是错在))\s*([^，。；;\n]{2,80})")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ：:，,。；;\n")


def extract_candidates(question: str, *, message_id: str = "") -> list[MemoryCandidate]:
    """只从用户原话提取，所有候选包含可定位的原文证据。"""
    text = (question or "").strip()
    if not text:
        return []
    out: list[MemoryCandidate] = []

    def add(kind: str, key: str, value: str, quote: str, confidence: float = 0.95):
        value = _clean(value)
        if not value or _SENSITIVE.search(value):
            return
        out.append(MemoryCandidate(kind, key, value, confidence, quote.strip(), message_id))

    for match in _GOAL.finditer(text):
        add("goal", "study_goal", match.group(1), match.group(0))
    for match in _TIME.finditer(text):
        add("constraint", "daily_minutes", match.group(1), match.group(0))
    for match in _WEAK.finditer(text):
        add("learning_signal", "weak_topic", match.group(1), match.group(0), 0.82)
    for match in _PREFERENCE.finditer(text):
        value = _clean(match.group(1))
        # 泛化请求和普通问题不是稳定偏好。
        if value and not value.endswith(("吗", "呢", "什么", "如何", "怎么")):
            add("preference", "teaching_preference", value, match.group(0), 0.82)
    return out


def fingerprint(candidate: MemoryCandidate) -> str:
    return f"{candidate.kind}|{candidate.key}|{candidate.value}|{candidate.evidence_quote}"
