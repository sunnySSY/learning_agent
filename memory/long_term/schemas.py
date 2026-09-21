"""长期记忆的稳定数据契约。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    key: str
    value: str
    confidence: float
    evidence_quote: str
    source_message_id: str = ""


@dataclass(frozen=True)
class ReviewItem:
    task_id: str
    knowledge_point_id: str
    subject: str
    name: str
    mastery_status: str
    due_at: str
    overdue_days: int
    estimated_minutes: int = 5


INTERVALS = (1, 3, 7, 14, 30)
RATINGS = {"again", "hard", "good", "easy"}
