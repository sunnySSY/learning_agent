"""SQLite metadata repository used by the Phase 5 service.

SQLite is the supported single-instance development backend.  The schema is
deliberately independent from the Phase 4 learning database so a Web reset or
test fixture cannot corrupt the memory/checkpoint stores.  Production can
replace this repository with PostgreSQL while retaining the service contract.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from functools import wraps
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from config import cfg


def synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def config_hash() -> str:
    payload = {
        "embedding_model": cfg.embed_model,
        "embedding_dimension": cfg.embed_dim,
        "splitter": "recursive_character_v1",
        "parser": "pdf_text_v1",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


class ProductRepository:
    def __init__(self, path: Path | None = None):
        raw_path = path or cfg.product_db
        self.path = Path(raw_path)
        if str(raw_path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(":memory:" if str(raw_path) == ":memory:" else str(self.path), check_same_thread=False)
        self._lock = threading.RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, auth_subject TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL,
                storage_key TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, index_status TEXT NOT NULL DEFAULT 'unindexed',
                active_index_version INTEGER, chunk_count INTEGER NOT NULL DEFAULT 0,
                index_config_hash TEXT NOT NULL, last_error_code TEXT, last_error_id TEXT,
                last_error_message TEXT, uploaded_at TEXT NOT NULL, index_updated_at TEXT,
                delete_requested_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS files_user_time ON files(user_id, uploaded_at DESC);
            CREATE TABLE IF NOT EXISTS index_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, file_id TEXT NOT NULL,
                target_version INTEGER NOT NULL, operation TEXT NOT NULL DEFAULT 'index',
                status TEXT NOT NULL DEFAULT 'queued', stage TEXT, attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3, idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, index_config_hash TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0, embedding_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0, error_code TEXT, error_id TEXT,
                error_message TEXT, created_at TEXT NOT NULL, started_at TEXT,
                heartbeat_at TEXT, finished_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(file_id) REFERENCES files(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency
                ON index_jobs(user_id, idempotency_key, operation);
            CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active
                ON index_jobs(file_id) WHERE status IN ('queued','running','cancel_requested');
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running', idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL DEFAULT '',
                vision_parse_version TEXT, image_context_json TEXT, recognized_text TEXT,
                confirmation_at TEXT, answer TEXT, citations_json TEXT, tool_calls_json TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS turns_idempotency
                ON turns(user_id, idempotency_key);
            CREATE TABLE IF NOT EXISTS image_attachments (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                storage_key TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                width INTEGER, height INTEGER, expires_at TEXT NOT NULL, deleted_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(turn_id) REFERENCES turns(id)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                object_type TEXT NOT NULL, object_id TEXT, result TEXT NOT NULL,
                request_id TEXT NOT NULL, source_ip_hash TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idempotency_operations (
                user_id TEXT NOT NULL, operation TEXT NOT NULL, object_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, response_json TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(user_id, operation, object_id, idempotency_key)
            );
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(turns)")}
        if "request_hash" not in columns:
            self.connection.execute("ALTER TABLE turns ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @synchronized
    def ensure_user(self, subject: str) -> str:
        # Internal IDs are UUIDs; the subject itself never appears in API data.
        user_id = str(uuid5(NAMESPACE_URL, f"study-agent:{subject}"))
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO users(id,auth_subject,created_at,last_seen_at) VALUES (?,?,?,?)",
                (user_id, subject, now, now),
            )
            self.connection.execute("UPDATE users SET last_seen_at=? WHERE id=?", (now, user_id))
        row = self.connection.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row[0] != "active":
            raise PermissionError("用户已停用")
        return user_id

    @synchronized
    def storage_usage(self, user_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM files WHERE user_id=? AND delete_requested_at IS NULL",
            (user_id,),
        ).fetchone()
        return int(row[0] or 0)

    @synchronized
    def insert_file(self, user_id: str, display_name: str, storage_key: str, media_type: str,
                    size_bytes: int, sha256: str, file_id: str | None = None) -> dict:
        file_id = file_id or str(uuid4())
        now = utc_now()
        self.connection.execute(
            "INSERT INTO files(id,user_id,display_name,storage_key,media_type,size_bytes,sha256,index_config_hash,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (file_id, user_id, display_name, storage_key, media_type, size_bytes, sha256, config_hash(), now),
        )
        self.connection.commit()
        return self.file(user_id, file_id)  # type: ignore[return-value]

    @staticmethod
    def _file_dict(row: sqlite3.Row) -> dict:
        result = dict(row)
        if result.get("index_status") == "indexed" and result.get("index_config_hash") != config_hash():
            result["index_status"] = "stale"
        return result

    @synchronized
    def file(self, user_id: str, file_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM files WHERE id=? AND user_id=? AND delete_requested_at IS NULL", (file_id, user_id)).fetchone()
        return self._file_dict(row) if row else None

    @synchronized
    def file_including_deleted(self, user_id: str, file_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone()
        return self._file_dict(row) if row else None

    @synchronized
    def list_files(self, user_id: str, status: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM files WHERE user_id=? AND delete_requested_at IS NULL"
        args: list[object] = [user_id]
        if status:
            sql += " AND index_status=?"; args.append(status)
        sql += " ORDER BY uploaded_at DESC LIMIT ? OFFSET ?"; args.extend([limit, offset])
        return [self._file_dict(row) for row in self.connection.execute(sql, args)]

    @synchronized
    def create_index_job(self, user_id: str, file_id: str, idempotency_key: str) -> tuple[dict, bool]:
        row = self.file(user_id, file_id)
        if not row:
            raise FileNotFoundError("file_not_found")
        request_hash = hashlib.sha256(f"{file_id}:{row['sha256']}:{config_hash()}".encode()).hexdigest()
        existing = self.connection.execute(
            "SELECT * FROM index_jobs WHERE user_id=? AND idempotency_key=? AND operation='index'",
            (user_id, idempotency_key),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise ValueError("idempotency_conflict")
            return dict(existing), True
        active = self.connection.execute(
            "SELECT * FROM index_jobs WHERE file_id=? AND status IN ('queued','running','cancel_requested') ORDER BY created_at DESC LIMIT 1",
            (file_id,),
        ).fetchone()
        if active:
            return dict(active), True
        running_count = self.connection.execute("SELECT COUNT(*) FROM index_jobs WHERE user_id=? AND operation='index' AND status IN ('queued','running','cancel_requested')", (user_id,)).fetchone()[0]
        if running_count >= 1:
            raise PermissionError("index_concurrency_limit")
        if row["index_status"] == "indexed" and row["sha256"] and row["index_config_hash"] == config_hash():
            # Persist a stable success result for a repeated request.
            job_id = str(uuid4()); now = utc_now()
            try:
                with self.connection:
                    self.connection.execute(
                        "INSERT INTO index_jobs(id,user_id,file_id,target_version,status,stage,max_attempts,idempotency_key,request_hash,index_config_hash,chunk_count,created_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (job_id,user_id,file_id,row["active_index_version"] or 1,"succeeded","finalizing",cfg.index_max_attempts,idempotency_key,request_hash,config_hash(),row["chunk_count"],now,now),
                    )
            except sqlite3.IntegrityError:
                existing = self.connection.execute("SELECT * FROM index_jobs WHERE user_id=? AND idempotency_key=? AND operation='index'", (user_id, idempotency_key)).fetchone()
                if existing: return dict(existing), True
                raise
            return dict(self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()), True
        today_count = self.connection.execute("SELECT COUNT(*) FROM index_jobs WHERE user_id=? AND operation='index' AND created_at>=date('now')", (user_id,)).fetchone()[0]
        if today_count >= cfg.index_daily_limit:
            raise PermissionError("index_daily_quota")
        estimated_tokens = max(1, int(row["size_bytes"] / 3))
        used_tokens = self.connection.execute("SELECT COALESCE(SUM(embedding_tokens),0) FROM index_jobs WHERE user_id=? AND operation='index' AND created_at>=date('now')", (user_id,)).fetchone()[0]
        if used_tokens + estimated_tokens > cfg.embedding_daily_token_limit:
            raise PermissionError("embedding_token_quota")
        target = int(row["active_index_version"] or 0) + 1
        job_id = str(uuid4()); now = utc_now()
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO index_jobs(id,user_id,file_id,target_version,status,stage,max_attempts,idempotency_key,request_hash,index_config_hash,embedding_tokens,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id,user_id,file_id,target,"queued","parsing",cfg.index_max_attempts,idempotency_key,request_hash,config_hash(),estimated_tokens,now),
                )
                self.connection.execute("UPDATE files SET index_status='queued',last_error_code=NULL,last_error_id=NULL,last_error_message=NULL WHERE id=? AND user_id=?", (file_id,user_id))
        except sqlite3.IntegrityError:
            existing = self.connection.execute("SELECT * FROM index_jobs WHERE user_id=? AND idempotency_key=? AND operation='index'", (user_id, idempotency_key)).fetchone()
            if existing: return dict(existing), True
            active = self.connection.execute("SELECT * FROM index_jobs WHERE file_id=? AND status IN ('queued','running','cancel_requested') ORDER BY created_at DESC LIMIT 1", (file_id,)).fetchone()
            if active: return dict(active), True
            raise
        return dict(self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()), False

    @synchronized
    def job(self, user_id: str, job_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        return dict(row) if row else None

    @synchronized
    def job_by_id(self, job_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def job_for_operation(self, job_id: str, operation: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=? AND operation=?", (job_id, operation)).fetchone()
        return dict(row) if row else None

    @synchronized
    def pending_jobs(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM index_jobs WHERE status='queued' ORDER BY created_at")]

    def recover_stale_jobs(self, timeout_seconds: int = 120) -> int:
        """Return abandoned running jobs to the queue after a worker restart."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        cutoff_text = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connection:
            rows = self.connection.execute("SELECT id,file_id,attempt,max_attempts FROM index_jobs WHERE status='running' AND heartbeat_at<?", (cutoff_text,)).fetchall()
            for row in rows:
                if int(row["attempt"]) >= int(row["max_attempts"]):
                    self.connection.execute("UPDATE index_jobs SET status='failed',error_code='worker_timeout',error_id=?,error_message='worker 执行超时，请重试',finished_at=? WHERE id=?", (str(uuid4()),utc_now(),row["id"]))
                    self.connection.execute("UPDATE files SET index_status='failed',last_error_code='worker_timeout',last_error_message='入库 worker 超时，请重试' WHERE id=?", (row["file_id"],))
                else:
                    self.connection.execute("UPDATE index_jobs SET status='queued',stage='parsing' WHERE id=?", (row["id"],))
        return len(rows)

    @synchronized
    def claim_job(self, job_id: str) -> dict | None:
        now = utc_now()
        with self.connection:
            cur = self.connection.execute(
                "UPDATE index_jobs SET status='running',stage='parsing',attempt=attempt+1,started_at=COALESCE(started_at,?),heartbeat_at=? WHERE id=? AND status='queued'",
                (now, now, job_id),
            )
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] != "running":
            return None
        self.connection.execute("UPDATE files SET index_status='indexing' WHERE id=?", (row["file_id"],)); self.connection.commit()
        return dict(row)

    @synchronized
    def update_job_stage(self, job_id: str, stage: str) -> None:
        now = utc_now(); self.connection.execute("UPDATE index_jobs SET stage=?,heartbeat_at=? WHERE id=?", (stage, now, job_id)); self.connection.commit()

    @synchronized
    def finish_job(self, job_id: str, *, success: bool, chunk_count: int = 0,
                   error_code: str | None = None, error_message: str | None = None) -> None:
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: return
        now = utc_now()
        with self.connection:
            if success:
                deleting = self.connection.execute("SELECT delete_requested_at FROM files WHERE id=?", (row["file_id"],)).fetchone()
                if deleting and deleting[0]:
                    self.connection.execute("UPDATE index_jobs SET status='cancelled',stage='finalizing',finished_at=? WHERE id=?", (now, job_id))
                    return
                self.connection.execute("UPDATE index_jobs SET status='succeeded',stage='finalizing',chunk_count=?,finished_at=?,heartbeat_at=? WHERE id=?", (chunk_count,now,now,job_id))
                self.connection.execute("UPDATE files SET index_status='indexed',active_index_version=?,chunk_count=?,index_config_hash=?,index_updated_at=?,last_error_code=NULL,last_error_id=NULL,last_error_message=NULL WHERE id=?", (row["target_version"],chunk_count,row["index_config_hash"],now,row["file_id"]))
            else:
                error_id = str(uuid4())
                self.connection.execute("UPDATE index_jobs SET status='failed',stage='finalizing',error_code=?,error_id=?,error_message=?,finished_at=? WHERE id=?", (error_code or "index_failed",error_id,error_message or "入库失败，请重试",now,job_id))
                self.connection.execute("UPDATE files SET index_status=?,last_error_code=?,last_error_id=?,last_error_message=? WHERE id=?", ("stale" if row["target_version"] > 1 else "failed",error_code or "index_failed",error_id,error_message or "入库失败，请重试",row["file_id"]))

    @synchronized
    def mark_delete(self, user_id: str, file_id: str, idempotency_key: str) -> tuple[dict, bool]:
        existing = self.connection.execute("SELECT * FROM index_jobs WHERE user_id=? AND idempotency_key=? AND operation='delete'", (user_id,idempotency_key)).fetchone()
        if existing: return dict(existing), True
        row = self.file(user_id, file_id)
        if not row: raise FileNotFoundError("file_not_found")
        job_id = str(uuid4()); now = utc_now(); request_hash = hashlib.sha256(f"delete:{file_id}".encode()).hexdigest()
        try:
            with self.connection:
                self.connection.execute("UPDATE files SET index_status='deleting',delete_requested_at=? WHERE id=? AND user_id=?", (now,file_id,user_id))
                self.connection.execute("UPDATE index_jobs SET status='cancelled',finished_at=? WHERE file_id=? AND status IN ('queued','running','cancel_requested')", (now,file_id))
                self.connection.execute("INSERT INTO index_jobs(id,user_id,file_id,target_version,operation,status,stage,max_attempts,idempotency_key,request_hash,index_config_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (job_id,user_id,file_id,row["active_index_version"] or 0,"delete","queued","finalizing",1,idempotency_key,request_hash,config_hash(),now))
        except sqlite3.IntegrityError:
            existing = self.connection.execute("SELECT * FROM index_jobs WHERE user_id=? AND idempotency_key=? AND operation='delete'", (user_id, idempotency_key)).fetchone()
            if existing: return dict(existing), True
            raise
        return dict(self.connection.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()), False

    @synchronized
    def complete_delete(self, job_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM index_jobs WHERE id=? AND operation='delete'", (job_id,)).fetchone()
        if not row: return None
        with self.connection:
            self.connection.execute("UPDATE index_jobs SET status='succeeded',finished_at=? WHERE id=?", (utc_now(),job_id))
            self.connection.execute("DELETE FROM image_attachments WHERE turn_id IN (SELECT id FROM turns WHERE user_id=?)", (row["user_id"],))
            # Keep a minimal tombstone so the delete task remains auditable;
            # normal file queries hide rows with delete_requested_at set.
            self.connection.execute("UPDATE files SET storage_key='',display_name='[deleted]',size_bytes=0,sha256='',chunk_count=0,active_index_version=NULL,index_status='deleting' WHERE id=? AND user_id=?", (row["file_id"],row["user_id"]))
        return dict(row)

    def delete_file_row(self, user_id: str, file_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM image_attachments WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM index_jobs WHERE file_id=? AND user_id=?", (file_id, user_id))
            self.connection.execute("DELETE FROM files WHERE id=? AND user_id=?", (file_id, user_id))

    def delete_user(self, user_id: str) -> int:
        rows = self.connection.execute("SELECT id FROM files WHERE user_id=?", (user_id,)).fetchall()
        with self.connection:
            self.connection.execute("DELETE FROM image_attachments WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM index_jobs WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM turns WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM files WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM users WHERE id=?", (user_id,))
        return len(rows)

    @synchronized
    def active_versions(self, user_id: str) -> dict[str, int]:
        rows = self.connection.execute("SELECT id,active_index_version FROM files WHERE user_id=? AND index_status IN ('indexed','stale') AND delete_requested_at IS NULL AND active_index_version IS NOT NULL", (user_id,)).fetchall()
        return {row["id"]: int(row["active_index_version"]) for row in rows}

    @synchronized
    def latest_job_for_file(self, file_id: str) -> dict | None:
        row = self.connection.execute("SELECT id,stage FROM index_jobs WHERE file_id=? ORDER BY created_at DESC LIMIT 1", (file_id,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def create_turn(self, user_id: str, thread_id: str, key: str, message: str = "") -> tuple[dict, bool]:
        request_hash = hashlib.sha256(f"{thread_id}:{message}".encode()).hexdigest()
        existing = self.connection.execute("SELECT * FROM turns WHERE user_id=? AND idempotency_key=?", (user_id,key)).fetchone()
        if existing:
            if existing["request_hash"] and existing["request_hash"] != request_hash:
                raise ValueError("idempotency_conflict")
            return dict(existing), True
        turn_id = str(uuid4()); now = utc_now()
        try:
            self.connection.execute("INSERT INTO turns(id,user_id,thread_id,idempotency_key,request_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (turn_id,user_id,thread_id,key,request_hash,now,now)); self.connection.commit()
        except sqlite3.IntegrityError:
            existing = self.connection.execute("SELECT * FROM turns WHERE user_id=? AND idempotency_key=?", (user_id,key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash: raise ValueError("idempotency_conflict")
                return dict(existing), True
            raise
        return dict(self.connection.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()), False

    @synchronized
    def turn(self, user_id: str, turn_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM turns WHERE id=? AND user_id=?", (turn_id,user_id)).fetchone(); return dict(row) if row else None

    @synchronized
    def update_turn(self, user_id: str, turn_id: str, **values) -> None:
        allowed = {"status","vision_parse_version","image_context_json","recognized_text","confirmation_at","answer","citations_json","tool_calls_json"}
        values = {k:v for k,v in values.items() if k in allowed}
        if not values: return
        values["updated_at"] = utc_now(); keys = list(values)
        self.connection.execute(f"UPDATE turns SET {','.join(k+'=?' for k in keys)} WHERE id=? AND user_id=?", [values[k] for k in keys] + [turn_id,user_id]); self.connection.commit()

    @synchronized
    def add_attachment(self, user_id: str, turn_id: str, storage_key: str, media_type: str, size: int, width: int, height: int, expires_at: str) -> str:
        attachment_id = str(uuid4()); self.connection.execute("INSERT INTO image_attachments(id,user_id,turn_id,storage_key,media_type,size_bytes,width,height,expires_at) VALUES (?,?,?,?,?,?,?,?,?)", (attachment_id,user_id,turn_id,storage_key,media_type,size,width,height,expires_at)); self.connection.commit(); return attachment_id

    @synchronized
    def expired_attachments(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM image_attachments WHERE deleted_at IS NULL AND expires_at<?", (utc_now(),))]

    def mark_attachment_deleted(self, attachment_id: str) -> None:
        self.connection.execute("UPDATE image_attachments SET deleted_at=? WHERE id=?", (utc_now(), attachment_id)); self.connection.commit()

    @synchronized
    def attachments_for_turn(self, user_id: str, turn_id: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM image_attachments WHERE user_id=? AND turn_id=? AND deleted_at IS NULL", (user_id, turn_id))]

    def thread_exists(self, user_id: str, thread_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM turns WHERE user_id=? AND thread_id=? LIMIT 1", (user_id,thread_id)).fetchone(); return bool(row)

    @synchronized
    def audit(self, actor: str, action: str, object_type: str, object_id: str | None, result: str, request_id: str, source_ip_hash: str | None = None) -> None:
        self.connection.execute("INSERT INTO audit_events(id,actor,action,object_type,object_id,result,request_id,source_ip_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid4()),actor,action,object_type,object_id,result,request_id,source_ip_hash,utc_now())); self.connection.commit()

    @synchronized
    def idempotent_result(self, user_id: str, operation: str, object_id: str, key: str) -> dict | None:
        row = self.connection.execute("SELECT response_json FROM idempotency_operations WHERE user_id=? AND operation=? AND object_id=? AND idempotency_key=?", (user_id, operation, object_id, key)).fetchone()
        return json.loads(row[0]) if row else None

    @synchronized
    def save_idempotent_result(self, user_id: str, operation: str, object_id: str, key: str, response: dict) -> None:
        self.connection.execute("INSERT OR IGNORE INTO idempotency_operations(user_id,operation,object_id,idempotency_key,response_json,created_at) VALUES (?,?,?,?,?,?)", (user_id, operation, object_id, key, json.dumps(response, ensure_ascii=False, default=str), utc_now())); self.connection.commit()
