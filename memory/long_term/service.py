"""长期记忆门面：把提取、档案、知识点与复习接口组合起来。"""

from .repository import LearningStore


class MemoryService:
    def __init__(self, repository: LearningStore):
        self.repository = repository

    def commit_turn(self, user_id: str, thread_id: str, turn_id: str, question: str, answer: str, request_id: str | None = None) -> list[dict]:
        self.repository.record_turn(user_id, thread_id, turn_id, question, answer, request_id)
        if not self.repository.auto_extract(user_id):
            return []
        return self.repository.extract_turn(user_id, turn_id, question)

    def list_memory(self, user_id: str) -> dict:
        return {"facts": self.repository.facts(user_id), "candidates": self.repository.candidates(user_id)}

    def set_fact(self, user_id: str, key: str, value: str, kind: str = "preference") -> str:
        return self.repository.set_fact(user_id, key, value, kind)

    def knowledge(self, user_id: str, subject: str | None = None) -> list[dict]:
        return self.repository.knowledge_rows(user_id, subject)

    def review_today(self, user_id: str, minutes: int | None = None):
        return self.repository.review_today(user_id, minutes)

    def review_plan(self, user_id: str, minutes: int | None = None):
        return self.repository.review_plan(user_id, minutes)
