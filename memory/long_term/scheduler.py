"""确定性的 interval_v1 复习调度门面。"""

from datetime import datetime

from .repository import LearningStore


class ReviewScheduler:
    def __init__(self, repository: LearningStore):
        self.repository = repository

    def today(self, user_id: str, minutes: int | None = None, now: datetime | None = None) -> dict:
        return self.repository.review_plan(user_id, minutes, now)

    def feedback(self, user_id: str, task_id: str, rating: str, request_id: str, completed_at: datetime | None = None) -> dict:
        return self.repository.feedback(user_id, task_id, rating, request_id, completed_at)

    def postpone(self, user_id: str, task_id: str, days: int) -> str:
        return self.repository.postpone(user_id, task_id, days)
