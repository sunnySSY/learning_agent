"""知识点与学习证据的门面。"""

from .repository import LearningStore


class KnowledgeService:
    def __init__(self, repository: LearningStore):
        self.repository = repository

    def add(self, user_id: str, subject: str, name: str) -> str:
        return self.repository.add_knowledge(user_id, subject, name)

    def list(self, user_id: str, subject: str | None = None) -> list[dict]:
        return self.repository.knowledge_rows(user_id, subject)

    def self_rate(self, user_id: str, knowledge_point_id: str, rating: int) -> None:
        self.repository.self_rate(user_id, knowledge_point_id, rating)

    def delete(self, user_id: str, knowledge_point_id: str) -> None:
        self.repository.delete_knowledge(user_id, knowledge_point_id)

    def add_event(self, user_id: str, knowledge_point_id: str, event_type: str, payload: dict, **kwargs) -> str:
        return self.repository.add_event(user_id, knowledge_point_id, event_type, payload, **kwargs)
