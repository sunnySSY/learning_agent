"""长期记忆、知识点和复习任务的事务性 repository。"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import cfg

from .database import open_learning_db
from .extraction import extract_candidates, fingerprint
from .schemas import INTERVALS, RATINGS, ReviewItem


UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


class LearningStore:
    def __init__(self, path: Path | None = None, timezone_name: str | None = None):
        self.connection = open_learning_db(path or cfg.learning_db)
        self.timezone_name = timezone_name or cfg.user_timezone

    def close(self) -> None:
        self.connection.close()

    def ensure_user(self, user_id: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO users(user_id, timezone) VALUES (?, ?)", (user_id, self.timezone_name)
        )
        self.connection.commit()

    def set_auto_extract(self, user_id: str, enabled: bool) -> None:
        self.ensure_user(user_id)
        self.connection.execute("UPDATE users SET auto_extract=? WHERE user_id=?", (int(enabled), user_id))
        self.connection.commit()

    def auto_extract(self, user_id: str) -> bool:
        self.ensure_user(user_id)
        return bool(self.connection.execute("SELECT auto_extract FROM users WHERE user_id=?", (user_id,)).fetchone()[0])

    def record_turn(self, user_id: str, thread_id: str, turn_id: str, question: str, answer: str, request_id: str | None = None) -> bool:
        self.ensure_user(user_id)
        cur = self.connection.execute(
            "INSERT OR IGNORE INTO turns(turn_id,request_id,user_id,thread_id,question,answer) VALUES (?,?,?,?,?,?)",
            (turn_id, request_id or turn_id, user_id, thread_id, question, answer),
        )
        self.connection.commit()
        return cur.rowcount == 1

    def extract_turn(self, user_id: str, turn_id: str, question: str | None = None, *, message_id: str = "") -> list[dict]:
        self.ensure_user(user_id)
        row = self.connection.execute("SELECT question FROM turns WHERE turn_id=? AND user_id=?", (turn_id, user_id)).fetchone()
        text = question if question is not None else (row[0] if row else "")
        generation = self.connection.execute("SELECT generation FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
        job_id = str(uuid4())
        existing_job = self.connection.execute("SELECT job_id,status FROM extraction_jobs WHERE turn_id=? AND extractor_version='rules_v1'", (turn_id,)).fetchone()
        if existing_job and existing_job["status"] == "succeeded":
            return []
        if existing_job:
            job_id = existing_job["job_id"]
            self.connection.execute("UPDATE extraction_jobs SET status='pending',attempts=attempts+1,error_code=NULL WHERE job_id=?", (job_id,))
        else:
            self.connection.execute("INSERT INTO extraction_jobs(job_id,user_id,turn_id,extractor_version,generation) VALUES (?,?,?,?,?)", (job_id,user_id,turn_id,"rules_v1",generation))
        candidates = extract_candidates(text, message_id=message_id or f"{turn_id}-user")
        rows = []
        for candidate in candidates:
            fp = fingerprint(candidate)
            status = "active" if candidate.confidence >= cfg.memory_auto_confirm_threshold else "pending"
            if candidate.confidence < cfg.memory_pending_threshold:
                status = "rejected"
            cid = str(uuid4())
            cur = self.connection.execute(
                "INSERT OR IGNORE INTO memory_candidates(candidate_id,user_id,kind,fact_key,value,confidence,evidence_quote,source_turn_id,fingerprint,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cid,user_id,candidate.kind,candidate.key,candidate.value,candidate.confidence,candidate.evidence_quote,turn_id,fp,status),
            )
            if cur.rowcount:
                rows.append({"candidate_id": cid, "status": status, "kind": candidate.kind, "key": candidate.key, "value": candidate.value, "confidence": candidate.confidence})
                if status == "active":
                    self._activate_candidate(user_id, cid)
        self.connection.execute("UPDATE extraction_jobs SET status='succeeded',attempts=1 WHERE job_id=?", (job_id,))
        self.connection.commit()
        return rows

    def _activate_candidate(self, user_id: str, candidate_id: str) -> str | None:
        row = self.connection.execute("SELECT * FROM memory_candidates WHERE candidate_id=? AND user_id=?", (candidate_id,user_id)).fetchone()
        if not row:
            return None
        old = self.connection.execute("SELECT fact_id,version,value FROM memory_facts WHERE user_id=? AND kind=? AND fact_key=? AND status='active'", (user_id,row["kind"],row["fact_key"])).fetchone()
        if old and old["value"] == row["value"]:
            self.connection.execute("INSERT INTO memory_sources(source_id,fact_id,turn_id,message_id,evidence_quote) VALUES (?,?,?,?,?)", (str(uuid4()),old["fact_id"],row["source_turn_id"],f"{row['source_turn_id']}-user" if row["source_turn_id"] else "",row["evidence_quote"]))
            self.connection.execute("UPDATE memory_candidates SET status='confirmed' WHERE candidate_id=?", (candidate_id,))
            return old["fact_id"]
        version = (old["version"] + 1) if old else 1
        if old:
            self.connection.execute("UPDATE memory_facts SET status='superseded',updated_at=CURRENT_TIMESTAMP WHERE fact_id=?", (old["fact_id"],))
        fact_id = str(uuid4())
        self.connection.execute("INSERT INTO memory_facts(fact_id,user_id,kind,fact_key,value,status,confidence,version,source_turn_id) VALUES (?,?,?,?,?,?,?,?,?)", (fact_id,user_id,row["kind"],row["fact_key"],row["value"],"active",row["confidence"],version,row["source_turn_id"]))
        self.connection.execute("INSERT INTO memory_sources(source_id,fact_id,turn_id,message_id,evidence_quote) VALUES (?,?,?,?,?)", (str(uuid4()),fact_id,row["source_turn_id"],f"{row['source_turn_id']}-user" if row["source_turn_id"] else "",row["evidence_quote"]))
        self.connection.execute("UPDATE memory_candidates SET status='confirmed' WHERE candidate_id=?", (candidate_id,))
        return fact_id

    def candidates(self, user_id: str) -> list[dict]:
        self.ensure_user(user_id)
        return [dict(row) for row in self.connection.execute("SELECT * FROM memory_candidates WHERE user_id=? AND status='pending' ORDER BY created_at", (user_id,))]

    def confirm_candidate(self, user_id: str, candidate_id: str) -> str:
        self.ensure_user(user_id)
        with self.connection:
            fact_id = self._activate_candidate(user_id, candidate_id)
        if not fact_id:
            raise ValueError("候选不存在、已处理或不属于当前用户")
        return fact_id

    def reject_candidate(self, user_id: str, candidate_id: str) -> None:
        cur = self.connection.execute("UPDATE memory_candidates SET status='rejected' WHERE candidate_id=? AND user_id=? AND status='pending'", (candidate_id,user_id))
        self.connection.commit()
        if cur.rowcount != 1:
            raise ValueError("候选不存在或已处理")

    def set_fact(self, user_id: str, key: str, value: str, kind: str = "preference") -> str:
        self.ensure_user(user_id)
        existing = self.connection.execute("SELECT fact_id FROM memory_facts WHERE user_id=? AND kind=? AND fact_key=? AND value=? AND status='active'", (user_id, kind, key, value)).fetchone()
        if existing:
            return existing[0]
        cid = str(uuid4())
        with self.connection:
            quote = "用户显式设置"
            self.connection.execute("INSERT INTO memory_candidates(candidate_id,user_id,kind,fact_key,value,confidence,evidence_quote,fingerprint,status) VALUES (?,?,?,?,?,?,?,?, 'pending')", (cid,user_id,kind,key,value,1.0,quote,f"explicit|{kind}|{key}|{value}"))
            # _activate_candidate 需要 source_turn_id，可为空
            return self._activate_candidate(user_id, cid)

    def facts(self, user_id: str) -> list[dict]:
        self.ensure_user(user_id)
        return [dict(row) for row in self.connection.execute("SELECT * FROM memory_facts WHERE user_id=? AND status='active' ORDER BY kind,fact_key", (user_id,))]

    def memory_context(self, user_id: str, limit: int = 10) -> str:
        facts = self.facts(user_id)[:limit]
        return "\n".join(f"- {row['kind']}/{row['fact_key']}: {row['value']}" for row in facts)

    def add_knowledge(self, user_id: str, subject: str, name: str) -> str:
        self.ensure_user(user_id)
        subject, name = subject.strip(), name.strip()
        if not subject or not name:
            raise ValueError("学科和知识点不能为空")
        kid = str(uuid4())
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO knowledge_points(knowledge_point_id,user_id,subject,name,normalized_name) VALUES (?,?,?,?,?)", (kid,user_id,subject,name,normalize(name)))
        row = self.connection.execute("SELECT knowledge_point_id FROM knowledge_points WHERE user_id=? AND subject=? AND normalized_name=?", (user_id,subject,normalize(name))).fetchone()
        return row[0]

    def knowledge(self, user_id: str, subject: str | None = None) -> list[dict]:
        self.ensure_user(user_id)
        if subject:
            rows = self.connection.execute("SELECT * FROM knowledge_points WHERE user_id=? AND subject=? ORDER BY name", (user_id,subject))
        else:
            rows = self.connection.execute("SELECT * FROM knowledge_points WHERE user_id=? ORDER BY subject,name", (user_id,))
        return [dict(row) for row in rows]

    def _create_event(self, user_id: str, kid: str, event_type: str, payload: dict, *, turn_id: str | None = None, reliability: float = 1.0) -> str:
        eid = str(uuid4())
        self.connection.execute("INSERT INTO learning_events(event_id,user_id,knowledge_point_id,turn_id,event_type,payload,reliability,occurred_at) VALUES (?,?,?,?,?,?,?,?)", (eid,user_id,kid,turn_id,event_type,json.dumps(payload,ensure_ascii=False),reliability,iso(now_utc())))
        self._recompute(kid)
        return eid

    def self_rate(self, user_id: str, kid: str, rating: int) -> None:
        if not 0 <= int(rating) <= 5:
            raise ValueError("自评等级必须在 0 到 5 之间")
        row = self.connection.execute("SELECT 1 FROM knowledge_points WHERE knowledge_point_id=? AND user_id=?", (kid,user_id)).fetchone()
        if not row:
            raise ValueError("知识点不存在或不属于当前用户")
        with self.connection:
            self.connection.execute("UPDATE knowledge_points SET self_rating=?,learning_status='reviewing',updated_at=CURRENT_TIMESTAMP WHERE knowledge_point_id=?", (rating,kid))
            self._create_event(user_id,kid,"self_report",{"rating":int(rating)},reliability=0.7)
            self.ensure_task(user_id,kid)

    def add_event(self, user_id: str, kid: str, event_type: str, payload: dict, *, turn_id: str | None = None, reliability: float = 1.0) -> str:
        if event_type not in {"explanation_seen","self_report","practice_correct","practice_incorrect","review_feedback"}:
            raise ValueError("不支持的学习事件类型")
        with self.connection:
            eid = self._create_event(user_id,kid,event_type,payload,turn_id=turn_id,reliability=reliability)
            self.ensure_task(user_id,kid)
        return eid

    def _recompute(self, kid: str) -> None:
        row = self.connection.execute("SELECT user_id FROM knowledge_points WHERE knowledge_point_id=?", (kid,)).fetchone()
        if not row:
            return
        events = self.connection.execute("SELECT * FROM learning_events WHERE knowledge_point_id=? AND revoked_at IS NULL ORDER BY occurred_at", (kid,)).fetchall()
        types = [event["event_type"] for event in events]
        learning = "unseen" if not events else ("practicing" if any(t.startswith("practice_") for t in types) else ("reviewing" if "review_feedback" in types or "self_report" in types else "exposed"))
        successes = [e for e in events if e["event_type"] in {"practice_correct","review_feedback"} and json.loads(e["payload"]).get("rating") in (None,"good","easy")]
        dates = {e["occurred_at"][:10] for e in successes}
        span_days = 0
        if successes:
            span_days = (parse_time(successes[-1]["occurred_at"]) - parse_time(successes[0]["occurred_at"])).days
        last_bad = any(e["event_type"] == "practice_incorrect" for e in events[-2:])
        mastery = "weak" if last_bad else ("stable" if len(successes) >= 3 and len(dates) >= 3 and span_days >= 7 else ("developing" if successes else "unknown"))
        self.connection.execute("UPDATE knowledge_points SET learning_status=?,mastery_status=?,updated_at=CURRENT_TIMESTAMP WHERE knowledge_point_id=?", (learning,mastery,kid))

    def ensure_task(self, user_id: str, kid: str, now: datetime | None = None) -> str:
        row = self.connection.execute("SELECT task_id FROM review_tasks WHERE user_id=? AND knowledge_point_id=? AND status='pending'", (user_id,kid)).fetchone()
        if row:
            return row[0]
        now = now or now_utc()
        try:
            local = now.astimezone(ZoneInfo(self.timezone_name))
        except Exception:
            local = now
        due_local = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        due = iso(due_local)
        tid = str(uuid4())
        self.connection.execute("INSERT INTO review_tasks(task_id,user_id,knowledge_point_id,due_at,original_due_at) VALUES (?,?,?,?,?)", (tid,user_id,kid,due,due))
        return tid

    def review_today(self, user_id: str, minutes: int | None = None, now: datetime | None = None) -> list[ReviewItem]:
        return self.review_plan(user_id, minutes, now)["items"]

    def review_plan(self, user_id: str, minutes: int | None = None, now: datetime | None = None) -> dict:
        self.ensure_user(user_id)
        now = now or now_utc()
        budget = minutes or cfg.review_daily_minutes
        rows = self.connection.execute("SELECT t.*,k.subject,k.name,k.mastery_status FROM review_tasks t JOIN knowledge_points k ON k.knowledge_point_id=t.knowledge_point_id WHERE t.user_id=? AND t.status='pending' AND t.due_at<=?", (user_id,iso(now))).fetchall()
        def key(row):
            overdue = max(0,(now-parse_time(row["due_at"])).days)
            weak = 0 if row["mastery_status"] == "weak" else 1
            return (-overdue, weak, row["subject"], row["due_at"], row["task_id"])
        result=[]
        backlog=[]
        used=0
        for row in sorted(rows,key=key):
            item=ReviewItem(row["task_id"],row["knowledge_point_id"],row["subject"],row["name"],row["mastery_status"],row["due_at"],max(0,(now-parse_time(row["due_at"])).days))
            if used + item.estimated_minutes <= budget:
                result.append(item); used += item.estimated_minutes
            else:
                backlog.append(item)
        return {"items": result, "backlog": backlog, "budget_minutes": budget, "used_minutes": used}

    def feedback(self, user_id: str, task_id: str, rating: str, request_id: str, completed_at: datetime | None = None) -> dict:
        rating = rating.lower()
        if rating not in RATINGS:
            raise ValueError("反馈必须是 again、hard、good 或 easy")
        self.ensure_user(user_id)
        existing = self.connection.execute("SELECT * FROM review_feedback WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            next_task = self.connection.execute("SELECT stage,due_at FROM review_tasks WHERE task_id=?", (existing["next_task_id"],)).fetchone()
            return {"task_id": existing["task_id"], "rating": existing["rating"], "next_task_id": existing["next_task_id"], "next_due_at": next_task["due_at"], "stage": next_task["stage"]}
        row = self.connection.execute("SELECT * FROM review_tasks WHERE task_id=? AND user_id=? AND status='pending'", (task_id,user_id)).fetchone()
        if not row:
            raise ValueError("复习任务不存在、已完成或不属于当前用户")
        completed_at = completed_at or now_utc()
        stage = int(row["stage"])
        if rating == "again": next_stage, days = 0, 1
        elif rating == "hard": next_stage, days = stage, max(1, math.ceil(INTERVALS[min(stage,4)]/2))
        elif rating == "good": next_stage, days = min(4,stage+1), INTERVALS[min(4,stage+1)]
        else: next_stage, days = min(4,stage+2), INTERVALS[min(4,stage+2)]
        try:
            local_completed = completed_at.astimezone(ZoneInfo(self.timezone_name))
        except Exception:
            local_completed = completed_at
        next_due = iso((local_completed + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0))
        next_id = str(uuid4()); fid = str(uuid4())
        with self.connection:
            self.connection.execute("UPDATE review_tasks SET status='completed',version=version+1 WHERE task_id=?", (task_id,))
            self.connection.execute("INSERT INTO review_feedback(feedback_id,task_id,request_id,rating,completed_at,next_task_id) VALUES (?,?,?,?,?,?)", (fid,task_id,request_id,rating,iso(completed_at),next_id))
            self.connection.execute("INSERT INTO review_tasks(task_id,user_id,knowledge_point_id,status,stage,due_at,original_due_at) VALUES (?,?,?,?,?,?,?)", (next_id,user_id,row["knowledge_point_id"],"pending",next_stage,next_due,next_due))
            self._create_event(user_id,row["knowledge_point_id"],"review_feedback",{"rating":rating,"stage":next_stage},reliability=0.8)
        return {"task_id":task_id,"rating":rating,"next_task_id":next_id,"next_due_at":next_due,"stage":next_stage}

    def postpone(self, user_id: str, task_id: str, days: int) -> str:
        if not 1 <= int(days) <= 7:
            raise ValueError("延期天数必须在 1 到 7 之间")
        row = self.connection.execute("SELECT due_at FROM review_tasks WHERE task_id=? AND user_id=? AND status='pending'", (task_id,user_id)).fetchone()
        if not row: raise ValueError("复习任务不存在或不属于当前用户")
        due = iso(parse_time(row[0]) + timedelta(days=int(days)))
        self.connection.execute("UPDATE review_tasks SET due_at=?,postponed_from=? WHERE task_id=?", (due,row[0],task_id)); self.connection.commit()
        return due

    def knowledge_rows(self, user_id: str, subject: str | None = None) -> list[dict]:
        return self.knowledge(user_id, subject)

    def delete_knowledge(self, user_id: str, knowledge_point_id: str) -> None:
        cur = self.connection.execute("DELETE FROM knowledge_points WHERE user_id=? AND knowledge_point_id=?", (user_id, knowledge_point_id))
        self.connection.commit()
        if cur.rowcount != 1:
            raise ValueError("知识点不存在或不属于当前用户")

    def delete_fact(self, user_id: str, fact_id: str) -> None:
        cur = self.connection.execute("DELETE FROM memory_facts WHERE fact_id=? AND user_id=?", (fact_id, user_id))
        self.connection.commit()
        if cur.rowcount != 1:
            raise ValueError("记忆不存在或不属于当前用户")

    def delete_thread_data(self, user_id: str, thread_id: str) -> dict:
        """删除会话衍生的长期数据，并重算仍有来源的事实。"""
        turns = [row[0] for row in self.connection.execute("SELECT turn_id FROM turns WHERE user_id=? AND thread_id=?", (user_id, thread_id))]
        with self.connection:
            if turns:
                marks = ",".join("?" for _ in turns)
                self.connection.execute(f"DELETE FROM memory_sources WHERE turn_id IN ({marks})", turns)
                self.connection.execute(f"DELETE FROM memory_candidates WHERE source_turn_id IN ({marks})", turns)
                self.connection.execute(f"DELETE FROM extraction_jobs WHERE turn_id IN ({marks})", turns)
                self.connection.execute(f"DELETE FROM learning_events WHERE turn_id IN ({marks})", turns)
                self.connection.execute(f"DELETE FROM turns WHERE turn_id IN ({marks})", turns)
                # 活跃事实没有来源时不能继续召回。
                self.connection.execute("DELETE FROM memory_facts WHERE user_id=? AND status='active' AND fact_id NOT IN (SELECT fact_id FROM memory_sources)", (user_id,))
        return {"turns": len(turns), "turn_ids": turns}

    def delete_user(self, user_id: str) -> dict:
        with self.connection:
            counts = {
                "facts": self.connection.execute("SELECT COUNT(*) FROM memory_facts WHERE user_id=?", (user_id,)).fetchone()[0],
                "knowledge": self.connection.execute("SELECT COUNT(*) FROM knowledge_points WHERE user_id=?", (user_id,)).fetchone()[0],
                "turns": self.connection.execute("SELECT COUNT(*) FROM turns WHERE user_id=?", (user_id,)).fetchone()[0],
            }
            for table in ("memory_sources", "memory_facts", "memory_candidates", "learning_events", "review_feedback", "review_tasks", "knowledge_points", "extraction_jobs", "turns", "users"):
                if table == "memory_sources":
                    self.connection.execute("DELETE FROM memory_sources WHERE fact_id NOT IN (SELECT fact_id FROM memory_facts)")
                else:
                    self.connection.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        return counts

    def export_rows(self, user_id: str, scope: str = "memory") -> dict:
        self.ensure_user(user_id)
        data: dict = {}
        if scope in {"memory", "all"}:
            data["memory_facts"] = [dict(row) for row in self.connection.execute("SELECT * FROM memory_facts WHERE user_id=?", (user_id,))]
            fact_ids = [row["fact_id"] for row in data["memory_facts"]]
            data["memory_sources"] = [] if not fact_ids else [dict(row) for row in self.connection.execute("SELECT * FROM memory_sources WHERE fact_id IN (" + ",".join("?" for _ in fact_ids) + ")", fact_ids)]
            for table in ("memory_candidates", "knowledge_points", "learning_events", "review_tasks"):
                data[table] = [dict(row) for row in self.connection.execute(f"SELECT * FROM {table} WHERE user_id=?", (user_id,))]
            task_ids = [row["task_id"] for row in data["review_tasks"]]
            data["review_feedback"] = [] if not task_ids else [dict(row) for row in self.connection.execute("SELECT * FROM review_feedback WHERE task_id IN (" + ",".join("?" for _ in task_ids) + ")", task_ids)]
        if scope in {"threads", "all"}:
            data["turns"] = [dict(row) for row in self.connection.execute("SELECT turn_id,request_id,thread_id,question,answer,status,created_at FROM turns WHERE user_id=?", (user_id,))]
        return data

    def create_deletion_job(self, user_id: str, scope: str, target: str | None, preview: dict) -> str:
        job_id = str(uuid4())
        self.connection.execute("INSERT INTO deletion_jobs(job_id,user_id,scope,target,preview_json) VALUES (?,?,?,?,?)", (job_id,user_id,scope,target,json.dumps(preview,ensure_ascii=False)))
        self.connection.commit()
        return job_id

    def finish_deletion_job(self, job_id: str, result: dict, error: str | None = None) -> None:
        self.connection.execute("UPDATE deletion_jobs SET status=?,result_json=?,error_code=?,finished_at=CURRENT_TIMESTAMP WHERE job_id=?", ("failed" if error else "succeeded",json.dumps(result,ensure_ascii=False),error,job_id))
        self.connection.commit()
